"""Load/save the bot's memory (state/state.json). Contains no secrets."""
import json
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "state", "state.json")
CONFIG_PATH = os.path.join(ROOT, "config.json")

RETAILERS = {"target": "Target", "wweshop": "WWEShop"}
EVENT_LABELS = {
    "preorder": "Preorder open",
    "restock": "Back in stock",
    "in_stock": "In stock",
    "sold_out": "Sold out",
    "announced": "Announced",
    "unknown": "Listed",
}


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def default_state(cfg):
    return {
        "version": 1,
        "seeded": False,
        "paused": False,
        "monthly_budget": cfg.get("default_monthly_budget", 150),
        "telegram": {"chat_id": None, "offset": 0},
        "figures": {},
        "purchases": [],
        "seen": [],
        "last_alerts": {},
        "events": [],
        "feeds": {},
        "robots": {},
        "last_run": 0,
        "last_commit": 0,
    }


def load_state(cfg, path=STATE_PATH):
    st = default_state(cfg)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            st.update(json.load(f))
    return st


def save_state(st, path=STATE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    st["seen"] = st["seen"][-3000:]
    st["events"] = st["events"][-300:]
    cutoff = time.time() - 30 * 86400
    st["last_alerts"] = {k: v for k, v in st["last_alerts"].items() if v > cutoff}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def slug(name):
    s = name.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unknown"


def month_key(ts=None):
    return time.strftime("%Y-%m", time.gmtime(ts or time.time()))


def spent_this_month(st, ts=None):
    mk = month_key(ts)
    return round(sum(p["price"] * p.get("qty", 1) for p in st["purchases"]
                     if month_key(p["ts"]) == mk), 2)


def is_purchased(st, fid):
    return any(p["figure_id"] == fid for p in st["purchases"])


def log_event(st, kind, text, **extra):
    st["events"].append({"ts": int(time.time()), "kind": kind, "text": text, **extra})


def find_figure(st, query):
    """Best match for a user-typed name: exact slug, then substring, then word overlap."""
    q = slug(query)
    if q in st["figures"]:
        return q
    hits = [fid for fid in st["figures"] if q in fid or fid in q]
    if len(hits) == 1:
        return hits[0]
    qw = set(q.split("-"))
    best, score = None, 0
    for fid in st["figures"]:
        s = len(qw & set(fid.split("-")))
        if s > score:
            best, score = fid, s
    return best if score and score == len(qw) else None


def upsert_figure(st, name, now=None):
    now = int(now or time.time())
    fid = slug(name)
    fig = st["figures"].get(fid)
    if not fig:
        fig = {"name": name, "first_seen": now, "release": None, "retailers": {}, "sources": []}
        st["figures"][fid] = fig
    return fid, fig
