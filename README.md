# WWE LJN Preorder & Restock Monitor

A personal bot that watches wrestling-figure news feeds for **WWE LJN** preorders,
restocks and new figures at **Target** and **WWEShop**, and texts you on Telegram
with the figure name, price, store and a direct buy link.

## How it works

```
Every ~10 min (GitHub Actions, free)
   └─ read public RSS news feeds (robots.txt checked, polite rate limits)
        └─ find LJN articles → pull figure names, prices, Target/WWEShop links
             └─ new preorder / restock / in-stock?  → Telegram alert
                  └─ save memory (state/state.json) + rebuild dashboard (docs/index.html)
```

**What it deliberately does not do**

- It never visits Target.com or WWEShop.com itself. Target's terms prohibit bots and
  unapproved buying agents; WWEShop's (Fanatics) terms prohibit automated data
  extraction. This is enforced in code (`never_fetch_hosts` in `config.json`).
- It never buys anything. Neither store authorizes third-party automated checkout,
  so `auto_checkout` stays disabled and there is no checkout code. You tap the link and
  check out yourself.
- Alerts are only as fast as the news sites post (usually minutes, sometimes longer).
  For the fastest restock news on one specific figure, also tap the store's own
  "Notify me" button on its product page.

## Telegram commands

| Command | What it does |
|---|---|
| `/status` | Running or paused, figure count, spending this month |
| `/list` | Figures being tracked |
| `/bought Hulk Hogan 26.99 target` | Log a purchase; mutes further alerts for that figure |
| `/unbought Hulk Hogan` | Undo a logged purchase |
| `/budget 200` | Set your monthly spending limit (alerts warn when you're over) |
| `/watch Randy Savage` | Track a figure by name before any store lists it |
| `/pause` or `/stop` | **Emergency stop** – no alerts at all |
| `/resume` | Alerts back on |
| `/test` | Send a sample alert |

The bot reads your messages each time it runs, so replies arrive within ~10 minutes.

## Emergency stop (three ways)

1. Text `/pause` to the bot.
2. GitHub → repo **Settings → Secrets and variables → Actions → Variables** → add
   `BOT_PAUSED` = `true`.
3. GitHub → **Actions → LJN monitor → ⋯ → Disable workflow** (stops it completely).

## Security

- The Telegram token lives only in GitHub **Secrets** (encrypted). It is never written to
  files, logs or the dashboard.
- The bot only answers the first Telegram chat that sends `/start` (or the chat in the
  optional `TELEGRAM_CHAT_ID` secret). Messages from anyone else are ignored.
- No store logins, passwords or card details are stored anywhere.

## Settings (`config.json`)

- `feeds` – news feeds to read (add more RSS feeds here)
- `default_monthly_budget` – starting budget (change anytime with `/budget`)
- `mute_alerts_when_over_budget` – `true` to silence alerts once you're over budget
- `duplicate_alert_window_hours` – don't repeat the same alert from another site within this window

## Run the tests

```
pip install -r requirements.txt pytest
python -m pytest -q
python -m monitor.main --dry-run   # one real check, prints alerts instead of sending
```
