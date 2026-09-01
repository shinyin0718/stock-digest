#!/usr/bin/env python3
"""
Daily stock/ETF digest.

Pulls yesterday's close-to-close move for a watchlist, works out which moves
are actually idiosyncratic (vs. the whole market moving), and attaches recent
headlines for the names that moved.

Free stack: yfinance (prices) + Google News RSS (headlines). No API keys.

    pip install yfinance feedparser requests pandas
    python daily_digest.py
"""

import os
import sys
import datetime as dt
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests
import yfinance as yf

# ---------------------------------------------------------------- config ----

# ticker -> name used to search for news
WATCHLIST = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "Nvidia",
    "TSM": "TSMC",
    "VOO": "Vanguard S&P 500 ETF",
    "QQQ": "Invesco QQQ",
    "IWM": "iShares Russell 2000",
    "GLD": "SPDR Gold Shares",
}

BENCHMARK = "SPY"          # used to separate market-wide moves from stock-specific ones
MOVE_THRESHOLD = 2.0       # abs % move that makes a name "notable"
RELATIVE_THRESHOLD = 1.5   # abs % move vs benchmark that makes it idiosyncratic
HEADLINES_PER_NAME = 4
NEWS_WINDOW_DAYS = 2

OUTPUT_DIR = "digests"

# --------------------------------------------------------------- prices -----


def fetch_prices(tickers):
    """Return {ticker: {'close', 'prev_close', 'pct', 'volume', 'avg_volume'}}."""
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
                continue
            close = float(df["Close"].iloc[-1])
            prev = float(df["Close"].iloc[-2])
            vol = float(df["Volume"].iloc[-1])
            avg_vol = float(df["Volume"].tail(20).mean())
            out[t] = {
                "date": df.index[-1].date(),
                "close": close,
                "prev_close": prev,
                "pct": (close / prev - 1) * 100,
                "volume": vol,
                "avg_volume": avg_vol,
                "vol_ratio": vol / avg_vol if avg_vol else float("nan"),
            }
        except (KeyError, IndexError, ValueError) as e:
            print(f"  ! price fetch failed for {t}: {e}", file=sys.stderr)
    return out


# ----------------------------------------------------------------- news -----


def fetch_news(name, ticker, limit=HEADLINES_PER_NAME):
    """Recent headlines from Google News RSS. No key, no rate limit in practice."""
    query = f'"{name}" OR {ticker} stock when:{NEWS_WINDOW_DAYS}d'
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
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "headline": headline,
                "publisher": publisher,
                "link": entry.get("link", ""),
            }
        )
        if len(items) >= limit:
            break
    return items


# --------------------------------------------------------------- report -----


def build_report(prices, bench_pct):
    lines = []
    date = next(iter(prices.values()))["date"] if prices else dt.date.today()

    lines.append(f"# Market digest — {date:%a %d %b %Y}")
    lines.append("")
    lines.append(f"**{BENCHMARK} {bench_pct:+.2f}%** — everything below is relative to that.")
    lines.append("")

    ranked = sorted(prices.items(), key=lambda kv: kv[1]["pct"], reverse=True)

    # Full table first, so you can always see the whole watchlist at a glance.
    lines.append("| Ticker | Close | Day | vs SPY | Vol |")
    lines.append("|---|---|---|---|---|")
    for t, p in ranked:
        rel = p["pct"] - bench_pct
        vol_flag = "🔺" if p["vol_ratio"] > 1.5 else ""
        lines.append(
            f"| {t} | {p['close']:,.2f} | {p['pct']:+.2f}% | "
            f"{rel:+.2f}% | {p['vol_ratio']:.1f}x {vol_flag} |"
        )
    lines.append("")

    # Then the "why" section, only for names that actually did something.
    notable = [
        (t, p)
        for t, p in ranked
        if abs(p["pct"]) >= MOVE_THRESHOLD
        or abs(p["pct"] - bench_pct) >= RELATIVE_THRESHOLD
    ]

    if not notable:
        lines.append("_Quiet day — nothing moved enough to look into._")
        return "\n".join(lines)

    lines.append("## What moved")
    lines.append("")
    for t, p in notable:
        rel = p["pct"] - bench_pct
        driver = "market-wide" if abs(rel) < RELATIVE_THRESHOLD else "stock-specific"
        lines.append(f"### {t} {p['pct']:+.2f}% ({rel:+.2f}% vs SPY — likely {driver})")
        if p["vol_ratio"] > 1.5:
            lines.append(f"Volume {p['vol_ratio']:.1f}x the 20-day average.")
        lines.append("")

        for item in fetch_news(WATCHLIST.get(t, t), t):
            pub = f" — _{item['publisher']}_" if item["publisher"] else ""
            lines.append(f"- [{item['headline']}]({item['link']}){pub}")
        lines.append("")

    lines.append("---")
    lines.append(
        "_Headlines are keyword-matched to the ticker, not verified causes. "
        "A story appearing next to a move is correlation, nothing more._"
    )
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
    tickers = list(WATCHLIST) + [BENCHMARK]
    print(f"Fetching prices for {len(tickers)} tickers...")
    prices = fetch_prices(tickers)

    if BENCHMARK not in prices:
        print("Could not fetch benchmark; aborting.", file=sys.stderr)
        return 1
    bench_pct = prices.pop(BENCHMARK)["pct"]

    if not prices:
        print("No price data returned.", file=sys.stderr)
        return 1

    print("Building report...")
    report = build_report(prices, bench_pct)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{dt.date.today():%Y-%m-%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Wrote {path}")

    if send_telegram(report):
        print("Sent to Telegram.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
