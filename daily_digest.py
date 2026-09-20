#!/usr/bin/env python3
"""
Daily stock/ETF digest.

Reads a watchlist from watchlist.csv, pulls the most recent completed trading
day's close-to-close move for each ticker, and sends a Telegram message: the
big movers with a one-line plain-language reason each, then a table of every
ticker with its % change and price.

Stack: yfinance (prices, free) + Google News RSS (headlines, free, no key) +
Google Gemini (summaries, free tier) + Telegram Bot API (delivery, free).

News and summaries are best-effort: if headlines or Gemini are unavailable the
price digest is still built and sent.

    pip install -r requirements.txt
    export TELEGRAM_TOKEN=...
    export TELEGRAM_CHAT_ID=...
    export GEMINI_API_KEY=...        # optional
    python daily_digest.py
"""

import csv
import os
import re
import sys
import time
import datetime as dt
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests
import yfinance as yf

from config import MOVE_THRESHOLD, WATCHLIST_FILE

OUTPUT_DIR = "digests"

HEADLINES_PER_NAME = 4
NEWS_WINDOW_DAYS = 2

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
# What the model replies with when the headlines don't explain the move.
NO_CLEAR_REASON = "NO_CLEAR_REASON"
FALLBACK_REASON = "No specific news found — may be general market movement."

# ------------------------------------------------------------ watchlist ----


def load_watchlist(path=None):
    """Return {ticker: name} from the CSV. Raises on missing/empty file."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), WATCHLIST_FILE)
    if not os.path.exists(path):
        raise SystemExit(
            f"Watchlist file not found: {path}\n"
            "Create it with a header line 'ticker,name' and one ticker per line."
        )

    tickers = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "ticker" not in [
            (c or "").strip().lower() for c in reader.fieldnames
        ]:
            raise SystemExit(
                f"{path} must have a header row containing a 'ticker' column."
            )
        key = next(c for c in reader.fieldnames if (c or "").strip().lower() == "ticker")
        name_key = next(
            (c for c in reader.fieldnames if (c or "").strip().lower() == "name"), None
        )
        for row in reader:
            ticker = (row.get(key) or "").strip().upper()
            if ticker and not ticker.startswith("#"):
                tickers[ticker] = (row.get(name_key) or "").strip() if name_key else ""

    if not tickers:
        raise SystemExit(f"No tickers found in {path}. Add at least one ticker.")
    return tickers


# --------------------------------------------------------------- prices -----


def fetch_prices(tickers):
    """
    Return {ticker: {'date', 'close', 'prev_close', 'pct'}}.

    The digest is sent pre-market (7 AM Malaysia time), so there is no live
    price to report. 'close' is the last completed trading day's closing price
    and 'pct' is that day's close-to-close change vs the day before it.
    """
    data = yf.download(
        tickers,
        period="1mo",
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
    )

    out = {}
    for t in tickers:
        try:
            df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
            df = df.dropna(subset=["Close"])
            if len(df) < 2:
                print(f"  ! not enough price history for {t}", file=sys.stderr)
                continue
            close = float(df["Close"].iloc[-1])
            prev = float(df["Close"].iloc[-2])
            out[t] = {
                "date": df.index[-1].date(),
                "close": close,
                "prev_close": prev,
                "pct": (close / prev - 1) * 100,
            }
        except (KeyError, IndexError, ValueError) as e:
            print(f"  ! price fetch failed for {t}: {e}", file=sys.stderr)
    return out


# ----------------------------------------------------------------- news -----


def fetch_news(name, ticker, limit=HEADLINES_PER_NAME):
    """Recent headlines from Google News RSS. No key needed; never raises."""
    query = f'"{name or ticker}" OR {ticker} stock when:{NEWS_WINDOW_DAYS}d'
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        feed = feedparser.parse(url)
    except Exception as e:  # noqa: BLE001 - never let news kill the run
        print(f"  ! news fetch failed for {ticker}: {e}", file=sys.stderr)
        return []

    items = []
    seen = set()
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        # Google appends " - Publisher" to titles
        headline, _, publisher = title.rpartition(" - ")
        headline = headline or title
        key = headline.lower()[:60]
        if not headline or key in seen:
            continue
        seen.add(key)
        items.append({"headline": headline, "publisher": publisher})
        if len(items) >= limit:
            break
    return items


# ------------------------------------------------------------ summaries ----


def gemini_client():
    """Return a Gemini client, or None if the key or SDK is unavailable."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    try:
        from google import genai
    except ImportError as e:
        print(f"  ! Gemini SDK unavailable: {e}", file=sys.stderr)
        return None
    try:
        return genai.Client(api_key=key)
    except Exception as e:  # noqa: BLE001 - never let the client kill the run
        print(f"  ! Gemini client failed: {e}", file=sys.stderr)
        return None


def pick_model(client, preferred=GEMINI_MODEL):
    """
    Return a usable model name. Google retires model names periodically, so
    fall back to the cheapest ('flash') model the key can actually see rather
    than losing every summary to a 404.
    """
    try:
        names = [
            m.name.split("/")[-1]
            for m in client.models.list()
            if "generateContent" in (getattr(m, "supported_actions", None) or [])
        ]
    except Exception as e:  # noqa: BLE001 - never let discovery kill the run
        print(f"  ! could not list Gemini models: {e}", file=sys.stderr)
        return preferred

    if preferred in names or not names:
        return preferred
    flash = [n for n in names if "flash" in n]
    chosen = sorted(flash or names, reverse=True)[0]
    print(f"  ! {preferred} unavailable; using {chosen}", file=sys.stderr)
    return chosen


def summarize_reason(ticker, name, pct, headlines, client=None, model=GEMINI_MODEL):
    """
    Ask Gemini for 1-2 plain-language sentences on why the stock likely moved,
    based only on the supplied headlines. Returns None when no summary can be
    produced (no client, no headlines, API error, or the headlines don't
    explain the move) so the caller can fall back instead of guessing.
    """
    if client is None or not headlines:
        return None

    headline_lines = "\n".join(
        f"- {h['headline']} ({h['publisher'] or 'unknown source'})" for h in headlines
    )

    prompt = f"""A stock moved on the last completed trading day. Here is the data and recent headlines mentioning it.

Ticker: {ticker} ({name or ticker})
Move: {pct:+.2f}%

Recent headlines:
{headline_lines}

Write 1-2 sentences a reader with no finance background can understand, \
explaining the likely reason for this move based only on the headlines above. \
Be concrete (name the actual event - earnings, guidance, an analyst call, \
product news, a macro or sector move) rather than generic, and use plain \
language with no jargon. Do not use hedging filler like "it appears" or "this \
suggests". Do not invent anything the headlines do not support: if they do not \
clearly explain a move of this size, reply with exactly {NO_CLEAR_REASON} and \
nothing else. Output only the explanation, no preamble."""

    text = ""
    # The free tier returns a transient 503 under load often enough to be worth
    # one retry.
    for attempt in range(2):
        try:
            resp = client.models.generate_content(model=model, contents=prompt)
            text = (resp.text or "").strip()
            break
        except Exception as e:  # noqa: BLE001 - never let summarization kill the run
            print(f"  ! summary failed for {ticker}: {e}", file=sys.stderr)
            if attempt == 0:
                time.sleep(3)
            else:
                return None

    if not text or NO_CLEAR_REASON in text:
        return None
    return " ".join(text.split())


def collect_reasons(movers, names, client=None):
    """Return {ticker: reason or None}, looking up news only for the movers."""
    model = pick_model(client) if (movers and client is not None) else GEMINI_MODEL
    reasons = {}
    for ticker, p in movers:
        headlines = fetch_news(names.get(ticker, ""), ticker)
        reasons[ticker] = summarize_reason(
            ticker,
            names.get(ticker, ""),
            p["pct"],
            headlines,
            client=client,
            model=model,
        )
    return reasons


# --------------------------------------------------------------- report -----


def _plain(text):
    """Drop characters Telegram's legacy Markdown would try to interpret."""
    return re.sub(r"[*_`\[\]]", "", text)


def build_report(prices, reasons=None, threshold=MOVE_THRESHOLD):
    """Telegram-friendly message: movers with reasons, then a monospace table."""
    reasons = reasons or {}
    date = next(iter(prices.values()))["date"] if prices else dt.date.today()

    lines = [
        f"📈 *Daily Movers — {date:%a %d %b %Y}*",
        "Last completed trading day's close-to-close move.",
        "",
    ]

    ranked = sorted(prices.items(), key=lambda kv: kv[1]["pct"], reverse=True)
    movers = sorted(
        (kv for kv in ranked if abs(kv[1]["pct"]) >= threshold),
        key=lambda kv: abs(kv[1]["pct"]),
        reverse=True,
    )

    if movers:
        no_news = []
        for t, p in movers:
            dot = "🟢" if p["pct"] > 0 else "🔴"
            lines.append(f"{dot} *{t}* {p['pct']:+.1f}% (${p['close']:,.2f})")
            reason = reasons.get(t)
            if not reason:
                reason = FALLBACK_REASON
                no_news.append(t)
            lines.append(f"Reason: {_plain(reason)}")
            lines.append("")
        if no_news:
            lines.append(f"— No major news found for: {', '.join(no_news)}")
            lines.append("")
    else:
        lines.append(f"Quiet day — no movers >= {threshold:g}%.")
        lines.append("")

    # Telegram does not render Markdown tables, so use a fixed-width code block.
    table = [f"{'Ticker':<8}{'Change':>9}{'Price':>12}", "-" * 29]
    for t, p in ranked:
        table.append(f"{t:<8}{p['pct']:>+8.2f}%{p['close']:>12,.2f}")

    lines.append("*Full watchlist*")
    lines.append("```")
    lines.extend(table)
    lines.append("```")

    return "\n".join(lines)


# ------------------------------------------------------------- delivery -----


def send_telegram(text):
    """Optional. Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID to enable."""
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False

    # Telegram caps messages at 4096 chars; split on section boundaries.
    chunks, current = [], ""
    for block in text.split("\n\n"):
        if len(current) + len(block) + 2 > 3900:
            chunks.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)

    for chunk in chunks:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if not r.ok:
            print(f"  ! telegram error {r.status_code}: {r.text}", file=sys.stderr)
            return False
    return True


# ----------------------------------------------------------------- main -----


def main():
    watchlist = load_watchlist()
    tickers = list(watchlist)
    print(f"Fetching prices for {len(tickers)} tickers...")
    prices = fetch_prices(tickers)

    if not prices:
        print("No price data returned.", file=sys.stderr)
        return 1

    movers = [(t, p) for t, p in prices.items() if abs(p["pct"]) >= MOVE_THRESHOLD]
    print(f"{len(movers)} mover(s) at or above {MOVE_THRESHOLD:g}%.")

    client = gemini_client()
    if movers and client is None:
        print("Gemini not configured (GEMINI_API_KEY); using fallback reasons.")
    reasons = collect_reasons(movers, watchlist, client=client)

    print("Building report...")
    report = build_report(prices, reasons)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{dt.date.today():%Y-%m-%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Wrote {path}")

    if send_telegram(report):
        print("Sent to Telegram.")
    else:
        print("Telegram not configured (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
