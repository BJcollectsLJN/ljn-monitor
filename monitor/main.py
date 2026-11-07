"""One monitoring pass. GitHub Actions runs this every ~10 minutes.

  python -m monitor.main            normal run
  python -m monitor.main --dry-run  print alerts instead of sending
"""
import argparse
import hashlib
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

from . import commands, dashboard
from .parse import extract, is_ljn, parse_feed
from .sources import Fetcher
from .state import (EVENT_LABELS, RETAILERS, is_purchased, load_config, load_state,
                    log_event, save_state, slug, spent_this_month, upsert_figure)
from .telegram import Telegram, esc

ALERT_EVENTS = {"preorder", "restock", "in_stock"}
EMOJI = {"preorder": "🟢 PREORDER OPEN", "restock": "🔁 BACK IN STOCK",
         "in_stock": "🛒 IN STOCK", "announced": "🆕 NEW FIGURE LISTED"}


def figure_for(st, name):
    """Merge a listing into an existing figure when it clearly matches."""
    s = slug(name)
    if s in st["figures"]:
        return s, st["figures"][s]
    for fid, f in st["figures"].items():
        if not f.get("manual"):
            continue
        kws = [slug(k) for k in f.get("keywords", [])] or [fid]
        if any(k in s or (s in k and len(s) > 6) for k in kws):
            return fid, f
    return upsert_figure(st, name)


def alert_text(fig, listing, source, over_budget):
    star = "⭐ ON YOUR WATCH LIST\n" if fig.get("manual") else ""
    lines = [f"{star}<b>{EMOJI.get(listing['event'], '📣')}</b>",
             f"<b>{esc(fig['name'])}</b> (WWE LJN)"]
    price = f"${listing['price']:.2f}" if listing.get("price") else "price not listed"
    lines.append(f"💲 {price} · 🏬 {RETAILERS[listing['retailer']]}")
    if listing.get("release") or fig.get("release"):
        lines.append(f"📅 Release: {esc(listing.get('release') or fig['release'])}")
    lines.append(f"🔗 <a href=\"{esc(listing['url'])}\">Buy at {RETAILERS[listing['retailer']]}</a>")
    lines.append(f"<i>Source: {esc(source['name'])}</i>")
    if over_budget:
        lines.append("⚠️ You're over this month's budget.")
    return "\n".join(lines)


def process_item(st, cfg, item, feed, outbox, now):
    if item["guid"] in st["seen"]:
        return
    st["seen"].append(item["guid"])
    if not is_ljn(item, cfg["must_match_all"]):
        return
    info = extract(item)
    seeded = st["seeded"]
    window = cfg.get("duplicate_alert_window_hours", 24) * 3600
    over = spent_this_month(st, now) >= st["monthly_budget"]

    if not info["listings"]:
        log_event(st, "news", item["title"], url=item["link"])
        if seeded:
            outbox.append(f"📰 <b>LJN news</b>\n{esc(item['title'])}\n<a href=\"{esc(item['link'])}\">Read article</a>"
                          f"\n<i>{esc(feed['name'])}</i>")
        return

    for L in info["listings"]:
        fid, fig = figure_for(st, L["name"])
        is_new_fig = not fig["retailers"]
        if L.get("release"):
            fig["release"] = L["release"]
        if item["link"] and item["link"] not in fig["sources"]:
            fig["sources"] = (fig["sources"] + [item["link"]])[-5:]
        r = fig["retailers"].setdefault(L["retailer"], {})
        r.update(url=L["url"], status=L["event"], updated=int(now))
        if L.get("price"):
            r["price"] = L["price"]

        event = L["event"]
        if event not in ALERT_EVENTS and not (is_new_fig and event in ("announced", "unknown")):
            continue
        if event == "unknown":
            L["event"] = event = "announced"
        key = f"{fid}|{L['retailer']}|{event}"
        if not seeded:
            st["last_alerts"][key] = now
            continue
        if now - st["last_alerts"].get(key, 0) < window:
            continue  # another site already reported this
        st["last_alerts"][key] = now
        label = f"{fig['name']} – {EVENT_LABELS[event]} at {RETAILERS[L['retailer']]}"
        if is_purchased(st, fid):
            log_event(st, "skipped", label + " (already bought)", url=L["url"])
            continue
        if over and cfg.get("mute_alerts_when_over_budget"):
            log_event(st, "skipped", label + " (over budget)", url=L["url"])
            continue
        log_event(st, "alert", label, url=L["url"])
        outbox.append(alert_text(fig, L, feed, over))


def fingerprint(st):
    keep = {k: st[k] for k in ("seeded", "paused", "monthly_budget", "telegram", "figures",
                               "purchases", "events", "robots")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()


def run(cfg, st, fetcher, tg, dry_run=False, now=None, hard_stop=False):
    now = now or time.time()
    before = fingerprint(st)
    env_chat = os.environ.get("TELEGRAM_CHAT_ID") or None
    replies = commands.handle_updates(st, tg, env_chat) if tg.enabled else []
    chat = env_chat or st["telegram"].get("chat_id")

    outbox = []
    for feed in cfg["feeds"]:
        text, status = fetcher.fetch(feed)
        print(f"[feed] {feed['name']}: {status}")
        if not text:
            continue
        try:
            items = parse_feed(text)
        except ET.ParseError:
            print(f"[feed] {feed['name']}: not valid XML")
            st["feeds"][feed["url"]]["status"] = "not valid XML"
            continue
        for item in items:
            process_item(st, cfg, item, feed, outbox, now)

    if not st["seeded"]:
        st["seeded"] = True
        log_event(st, "system", f"First run: indexed {len(st['figures'])} figures without alerting")
        outbox = [f"🤖 LJN monitor is online. Tracking {len(st['figures'])} figures from "
                  f"{len(cfg['feeds'])} news feeds. Send /help for commands."]

    if st.pop("_send_test", False):
        outbox.append("<b>🟢 TEST ALERT</b>\n<b>Example Figure</b> (WWE LJN)\n💲 $24.99 · 🏬 Target\n"
                      "🔗 <a href=\"https://www.target.com/\">Buy at Target</a>\n<i>This is only a test.</i>")

    paused = st["paused"] or hard_stop
    for c, text in replies:
        _send(tg, c, text, dry_run)
    if paused and outbox:
        print(f"[alerts] paused: {len(outbox)} alert(s) not sent")
        log_event(st, "system", f"{len(outbox)} alert(s) held back while paused")
    else:
        for text in outbox:
            _send(tg, chat, text, dry_run)
        if outbox and tg.enabled and not chat and not dry_run:
            print("[alerts] no Telegram chat linked yet – send /start to your bot")

    st["last_run"] = int(now)
    changed = fingerprint(st) != before
    return changed, outbox


def _send(tg, chat, text, dry_run):
    if dry_run or not tg.enabled:
        print("---- ALERT ----\n" + text)
    else:
        tg.send(chat, text)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config()
    st = load_state(cfg)
    hard_stop = os.environ.get("BOT_PAUSED", "").strip().lower() in ("1", "true", "yes")
    tg = Telegram()
    if not tg.enabled:
        print("[telegram] TELEGRAM_BOT_TOKEN not set – alerts will be printed only")
    else:
        me = tg._call("getMe")
        if me:
            print(f"[telegram] connected as @{me.get('username')}")
    changed, _ = run(cfg, st, Fetcher(cfg, st), tg, dry_run=args.dry_run, hard_stop=hard_stop)
    hourly = time.time() - st.get("last_commit", 0) > 3600
    if changed or hourly:
        st["last_commit"] = int(time.time())
        save_state(st)
        dashboard.write(st, cfg)
        print("[state] saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
