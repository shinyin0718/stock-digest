#!/usr/bin/env python3
"""
Daily stock/ETF digest (Phase 1).

Reads a watchlist from watchlist.csv, pulls the most recent completed trading
day's close-to-close move for each ticker, and sends a short Telegram message:
the big movers first, then a table of every ticker with its % change and
current price.

Stack: yfinance (prices, free) + Telegram Bot API (delivery, free).

    pip install -r requirements.txt
    export TELEGRAM_TOKEN=...
    export TELEGRAM_CHAT_ID=...
    python daily_digest.py
"""

import csv
import os
import sys
import datetime as dt

import pandas as pd
import requests
import yfinance as yf

from config import MOVE_THRESHOLD, WATCHLIST_FILE

OUTPUT_DIR = "digests"

# ------------------------------------------------------------ watchlist ----


def load_watchlist(path=None):
    """Return the list of tickers from the CSV. Raises on missing/empty file."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), WATCHLIST_FILE)
    if not os.path.exists(path):
        raise SystemExit(
            f"Watchlist file not found: {path}\n"
            "Create it with a header line 'ticker,name' and one ticker per line."
        )

    tickers = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "ticker" not in [
            (c or "").strip().lower() for c in reader.fieldnames
        ]:
            raise SystemExit(
                f"{path} must have a header row containing a 'ticker' column."
            )
        key = next(c for c in reader.fieldnames if (c or "").strip().lower() == "ticker")
        for row in reader:
            ticker = (row.get(key) or "").strip().upper()
            if ticker and not ticker.startswith("#"):
                tickers.append(ticker)

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


# --------------------------------------------------------------- report -----


def build_report(prices, threshold=MOVE_THRESHOLD):
    """Telegram-friendly message: movers first, then a monospace table."""
    date = next(iter(prices.values()))["date"] if prices else dt.date.today()

    lines = [
        f"*Market digest — {date:%a %d %b %Y}*",
        "Last completed trading day's close-to-close move.",
        "",
    ]

    ranked = sorted(prices.items(), key=lambda kv: kv[1]["pct"], reverse=True)
    movers = [(t, p) for t, p in ranked if abs(p["pct"]) >= threshold]

    lines.append(f"*Big movers (>= {threshold:g}%)*")
    if movers:
        for t, p in movers:
            arrow = "▲" if p["pct"] > 0 else "▼"
            lines.append(f"{arrow} {t}  {p['pct']:+.2f}%  ({p['close']:,.2f})")
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
    tickers = load_watchlist()
    print(f"Fetching prices for {len(tickers)} tickers...")
    prices = fetch_prices(tickers)

    if not prices:
        print("No price data returned.", file=sys.stderr)
        return 1

    print("Building report...")
    report = build_report(prices)

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
