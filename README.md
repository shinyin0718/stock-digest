# stock-digest

A tiny robot that checks your list of stocks and ETFs once a day and sends you a
Telegram message at **7:00 AM Malaysia time, Monday to Friday**.

The message has two parts:

1. **Big movers** — anything that moved 2% or more (up or down), each with a
   one-line plain-language reason worked out from recent news headlines.
2. **Full watchlist** — every ticker with its % change and price.

It looks like this:

```
📈 Daily Movers — Fri 18 Sep 2026
Last completed trading day's close-to-close move.

🔴 TSLA -3.2% ($364.27)
Reason: Tesla fell after it delivered fewer cars than expected last quarter.

🟢 AAPL +2.5% ($336.13)
Reason: Apple rose after a strong iPhone launch weekend.

— No major news found for: QQQ

Full watchlist
Ticker     Change       Price
-----------------------------
AAPL       +2.50%      336.13
...
```

News is only looked up for the big movers, never for the whole list. If no
headline explains a move, the line says *"No specific news found — may be
general market movement."* rather than guessing a reason.

Because the message arrives before the US market opens, the "% change" is the
move on the **last completed trading day** (its closing price vs. the closing
price the day before).

Everything runs for free on GitHub Actions. You don't need to keep your computer
on.

---

## 1. Change which stocks you track

Open [`watchlist.csv`](watchlist.csv) here on GitHub, click the pencil
(✏️ Edit) icon, and edit the list. It looks like this:

```
ticker,name
AAPL,Apple
MSFT,Microsoft
VOO,Vanguard S&P 500 ETF
QQQ,Invesco QQQ
TSLA,Tesla
```

- One ticker per line.
- The first line (`ticker,name`) is the header — leave it there.
- `name` is just a label for you; only `ticker` matters to the program.
- Use the symbol exactly as it appears on Yahoo Finance (e.g. `BRK-B`, not `BRK.B`).

When you're done, scroll down and click **Commit changes**. The next digest uses
the new list.

## 2. Change the "big mover" threshold

Open [`config.py`](config.py), click the pencil icon, and change this line:

```python
MOVE_THRESHOLD = 2.0
```

`2.0` means "flag anything that moved 2% or more". Set it to `1.5` to be more
sensitive, or `3.0` to see fewer names. Commit the change.

This threshold also decides which tickers get a news summary — only the flagged
movers do.

## 3. Change the time it's sent

Open `.github/workflows/daily-digest.yml` and edit this line:

```yaml
    - cron: "37 22 * * 0-4"
```

Times there are in UTC. Malaysia time is UTC+8, so 06:37 MYT = 22:37 UTC the
**previous** day — that's why the schedule says Sunday–Thursday (`0-4`) but you
receive it Monday–Friday. GitHub's scheduler is best-effort, so the message may
arrive late — on-the-hour slots often run 1–2 hours behind, which is why the
schedule uses an odd minute and aims a little before 7:00.

---

## How the Telegram setup works (already done)

You only need to redo this if you change bots or want the digest sent somewhere else.

1. **Bot token** — in Telegram, message [@BotFather](https://t.me/BotFather),
   send `/newbot`, and follow the prompts. BotFather replies with a token that
   looks like `123456789:AAH...`. That's the `TELEGRAM_TOKEN`.
2. **Chat ID** — send any message to your new bot first, then open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and look
   for `"chat":{"id":1506040458,...}`. That number is the `TELEGRAM_CHAT_ID`
   (currently `1506040458`).

### Storing them safely in GitHub

The token is a password — it must never be typed into a file in this repo.
It lives in GitHub's encrypted secrets instead:

1. Go to the repo → **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret**.
3. Name: `TELEGRAM_TOKEN`, Secret: the token from BotFather. Click **Add secret**.
4. Repeat with Name: `TELEGRAM_CHAT_ID`, Secret: `1506040458`.

Both secrets are already set up.

---

## How the news summaries work

Headlines come from Google News' free RSS feed (no account, no key). Turning
them into one plain sentence is done by **Google Gemini**, on its free tier.

The summaries are a bonus, never a blocker: if the Gemini key is missing,
out of quota, or the service is down, the digest still goes out on time with
the price table — those movers just show the "no specific news found" line.

### Getting a free Gemini key (already done)

1. Go to [Google AI Studio](https://aistudio.google.com/apikey) and sign in
   with a Google account.
2. Click **Create API key** and copy the key.
3. In the repo: **Settings** → **Secrets and variables** → **Actions** →
   **New repository secret**.
4. Name: `GEMINI_API_KEY`, Secret: the key you copied. Click **Add secret**.

The key is already stored as the `GEMINI_API_KEY` secret.

---

## Send yourself a test message right now

1. Go to the **Actions** tab at the top of the repo.
2. Click **Daily digest** in the left sidebar.
3. Click the **Run workflow** button on the right, then **Run workflow** in the
   little dropdown.
4. Refresh after a few seconds — a new run appears. Click it, then click the
   `digest` job to watch the logs. It takes under a minute.
5. Check Telegram. If the log ends with `Sent to Telegram.` the message was
   delivered.

If nothing arrives, open the run's log and look at the `Send digest` step:

- `Telegram not configured` → one of the two secrets is missing or misspelled.
- `telegram error 400: ... chat not found` → the chat ID is wrong, or you never
  sent the bot a message first.
- `Watchlist file not found` / `No tickers found` → check `watchlist.csv` still
  has the `ticker` header and at least one ticker.
- `Gemini not configured` / `summary failed` → the `GEMINI_API_KEY` secret is
  missing or the free-tier quota ran out. The message is still sent; only the
  reason lines are affected.
- `... unavailable; using ...` → Google retired the model the script asks for
  and it picked another one automatically. To pin a specific model, add a
  `GEMINI_MODEL` repository secret (or env var) with the model name.

---

## Running it on your own computer (optional)

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=123456789:AAH...
export TELEGRAM_CHAT_ID=1506040458
export GEMINI_API_KEY=...      # optional; without it you just get prices
python daily_digest.py
```

Each run also saves a copy of the message in `digests/` as a dated file.
