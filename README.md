# stock-digest

A tiny robot that checks your list of stocks and ETFs once a day and sends you a
Telegram message at **7:00 AM Malaysia time, Monday to Friday**.

The message has two parts:

1. **Big movers** — anything that moved 2% or more (up or down).
2. **Full watchlist** — every ticker with its % change and price.

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

## 3. Change the time it's sent

Open `.github/workflows/daily-digest.yml` and edit this line:

```yaml
    - cron: "0 23 * * 0-4"
```

Times there are in UTC. Malaysia time is UTC+8, so 07:00 MYT = 23:00 UTC the
**previous** day — that's why the schedule says Sunday–Thursday (`0-4`) but you
receive it Monday–Friday. GitHub's scheduler is best-effort, so the message may
arrive a few minutes late.

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

---

## Running it on your own computer (optional)

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=123456789:AAH...
export TELEGRAM_CHAT_ID=1506040458
python daily_digest.py
```

Each run also saves a copy of the message in `digests/` as a dated file.
