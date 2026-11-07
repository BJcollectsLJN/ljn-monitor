"""Telegram commands you can text the bot. Read once per run (so replies arrive
within one check interval, ~10 minutes)."""
import re
import time

from .state import (RETAILERS, EVENT_LABELS, find_figure, is_purchased, log_event,
                    month_key, spent_this_month, upsert_figure)
from .telegram import esc

HELP = (
    "<b>LJN Alert Bot</b>\n"
    "/status – overview and spending\n"
    "/list – figures being tracked\n"
    "/bought NAME PRICE [target|wweshop] – log a purchase (stops repeat alerts)\n"
    "/unbought NAME – remove that purchase\n"
    "/budget AMOUNT – set monthly spending limit\n"
    "/watch NAME – add a figure to track by hand\n"
    "/pause – emergency stop (no alerts)\n"
    "/resume – turn alerts back on\n"
    "/test – send a test alert\n\n"
    "Replies come within ~10 minutes (the bot checks in on a schedule)."
)


def handle_updates(st, tg, allowed_chat_env=None):
    """Process pending messages. Returns list of (chat_id, reply)."""
    replies = []
    ups = tg.updates(st["telegram"].get("offset", 0))
    for u in ups:
        st["telegram"]["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat or not text:
            continue
        linked = allowed_chat_env or st["telegram"].get("chat_id")
        if linked and str(chat) != str(linked):
            continue  # ignore strangers
        if not linked:
            if (msg.get("chat") or {}).get("type", "private") != "private":
                continue
            st["telegram"]["chat_id"] = chat  # first private chat to message the bot
            log_event(st, "system", "Telegram chat linked")
            replies.append((chat, "✅ Linked! This chat will get LJN alerts.\n\n" + HELP))
            continue
        if not text.startswith("/"):
            replies.append((chat, "Hi! Send /help to see what I can do."))
            continue
        replies.append((chat, run_command(st, text)))
    return replies


def run_command(st, text):
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower().split("@")[0]
    arg = arg.strip()
    if cmd in ("/start", "/help"):
        return HELP
    if cmd in ("/pause", "/stop"):
        st["paused"] = True
        log_event(st, "system", "Paused from Telegram")
        return "🛑 Paused. No alerts until you send /resume."
    if cmd == "/resume":
        st["paused"] = False
        log_event(st, "system", "Resumed from Telegram")
        return "▶️ Alerts are back on."
    if cmd == "/status":
        return status_text(st)
    if cmd == "/list":
        return list_text(st)
    if cmd == "/budget":
        m = re.search(r"\d+(?:\.\d+)?", arg)
        if not m:
            return f"Monthly budget is ${st['monthly_budget']:.2f}. Change it with /budget 200"
        st["monthly_budget"] = float(m.group())
        return f"💰 Monthly budget set to ${st['monthly_budget']:.2f}."
    if cmd == "/watch":
        if not arg:
            return "Usage: /watch Randy Savage"
        fid, fig = upsert_figure(st, arg[:60])
        fig["manual"] = True
        return f"👀 Now watching <b>{esc(fig['name'])}</b>. You'll get an alert when a news feed lists it."
    if cmd == "/bought":
        return bought(st, arg)
    if cmd == "/unbought":
        fid = find_figure(st, arg) if arg else None
        before = len(st["purchases"])
        st["purchases"] = [p for p in st["purchases"] if p["figure_id"] != fid]
        if fid and len(st["purchases"]) < before:
            return f"Removed purchase of {esc(st['figures'][fid]['name'])}."
        return "Couldn't find that purchase. Try /list to see names."
    if cmd == "/test":
        st["_send_test"] = True
        return "Sending a test alert…"
    return "Unknown command.\n\n" + HELP


def bought(st, arg):
    m = re.match(r"(.+?)\s+\$?(\d+(?:\.\d{1,2})?)\s*(target|wweshop|wwe shop)?\s*$", arg, re.I)
    if not m:
        return "Usage: /bought Hulk Hogan 26.99 target"
    name, price, ret = m.group(1), float(m.group(2)), (m.group(3) or "").lower().replace(" ", "")
    fid = find_figure(st, name)
    if not fid:
        fid, _ = upsert_figure(st, name)
    fig = st["figures"][fid]
    dup = is_purchased(st, fid)
    st["purchases"].append({"figure_id": fid, "name": fig["name"], "price": price, "qty": 1,
                            "retailer": ret or None, "ts": int(time.time())})
    log_event(st, "purchase", f"Bought {fig['name']} for ${price:.2f}")
    spent, budget = spent_this_month(st), st["monthly_budget"]
    msg = f"🧾 Logged <b>{esc(fig['name'])}</b> – ${price:.2f}. Restock alerts for it are now muted."
    if dup:
        msg += "\n⚠️ Heads up: you'd already logged this figure before."
    msg += f"\nThis month: ${spent:.2f} of ${budget:.2f}."
    if spent > budget:
        msg += "\n🚨 Over your monthly budget!"
    return msg


def status_text(st):
    spent, budget = spent_this_month(st), st["monthly_budget"]
    last = time.strftime("%b %d %H:%M UTC", time.gmtime(st.get("last_run") or time.time()))
    return (f"<b>Status:</b> {'🛑 PAUSED' if st['paused'] else '🟢 running'}\n"
            f"Tracking {len(st['figures'])} figures, {len(st['purchases'])} purchases logged\n"
            f"Spent in {month_key()}: ${spent:.2f} / ${budget:.2f}\n"
            f"Last check: {last}")


def list_text(st):
    if not st["figures"]:
        return "No figures tracked yet."
    rows = []
    figs = sorted(st["figures"].items(), key=lambda kv: -kv[1]["first_seen"])[:40]
    for fid, f in figs:
        bits = []
        for r, info in f["retailers"].items():
            bits.append(f"{RETAILERS.get(r, r)}: {EVENT_LABELS.get(info.get('status'), '?')}")
        mark = "✅ " if is_purchased(st, fid) else ""
        rows.append(f"{mark}<b>{esc(f['name'])}</b> – {esc(', '.join(bits) or 'watching')}")
    return "\n".join(rows)
