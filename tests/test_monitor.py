import copy
import os
import re

import pytest

from monitor import commands, dashboard
from monitor.main import run
from monitor.parse import extract, is_ljn, parse_feed
from monitor.sources import Fetcher
from monitor.state import default_state, find_figure, load_config, upsert_figure

HERE = os.path.dirname(__file__)
FEED = open(os.path.join(HERE, "fixtures", "feed.xml"), encoding="utf-8").read()
CFG = load_config()


class Resp:
    def __init__(self, code=200, text="", headers=None):
        self.status_code, self.text, self.headers = code, text, headers or {}


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        for pat, resp in self.routes.items():
            if pat in url:
                return resp() if callable(resp) else resp
        return Resp(404)


class FakeTG:
    def __init__(self, updates=None):
        self.sent, self._updates = [], updates or []
        self.enabled = True

    def updates(self, offset):
        u, self._updates = [x for x in self._updates if x["update_id"] >= offset], []
        return u

    def send(self, chat, text):
        self.sent.append((chat, text))


def msg(uid, chat, text):
    return {"update_id": uid, "message": {"chat": {"id": chat}, "text": text}}


def one_feed_cfg(url="https://news.test/feed/"):
    cfg = copy.deepcopy(CFG)
    cfg["feeds"] = [{"name": "Test News", "url": url}]
    return cfg


def fetcher(cfg, st, body=FEED, robots="User-agent: *\nAllow: /\n"):
    sess = FakeSession({"robots.txt": Resp(200, robots), "/feed/": Resp(200, body)})
    return Fetcher(cfg, st, session=sess, now=lambda: 1_000_000), sess


# ---------------- parsing ----------------
def test_parse_and_extract():
    items = parse_feed(FEED)
    assert len(items) == 4
    a = extract(items[0])
    names = {l["name"]: l for l in a["listings"]}
    assert set(names) == {"Hulk Hogan", "Hart Foundation Tag Team 2-Pack", "Jey Uso"}
    assert names["Hulk Hogan"]["price"] == 26.99
    assert names["Jey Uso"]["price"] == 24.99
    assert all(l["retailer"] == "target" and l["event"] == "preorder" for l in a["listings"])
    assert names["Hulk Hogan"]["release"] == "May 1, 2027"

    b = extract(items[1])["listings"][0]
    assert (b["name"], b["retailer"], b["event"], b["price"]) == ("CM Punk", "wweshop", "restock", 24.99)

    assert extract(items[2])["listings"] == []
    assert not is_ljn(items[3], ["ljn", "wwe"])
    assert not is_ljn({"title": "Thundercats LJN reissue preorder", "html": ""}, ["ljn", "wwe"])


def test_fuzzy_names_do_not_confuse_wrestlers():
    st = default_state(CFG)
    upsert_figure(st, "Jimmy Uso")
    assert find_figure(st, "Jey Uso") is None
    assert find_figure(st, "jimmy") == "jimmy-uso"


# ---------------- end-to-end runs ----------------
def test_first_run_seeds_without_alert_spam_then_alerts_new_items():
    cfg = one_feed_cfg()
    st = default_state(cfg)
    st["telegram"]["chat_id"] = 42
    f, _ = fetcher(cfg, st)
    tg = FakeTG()
    run(cfg, st, f, tg, now=1_000_000)
    assert len(tg.sent) == 1 and "online" in tg.sent[0][1]
    assert {"hulk-hogan", "cm-punk", "jey-uso"} <= set(st["figures"])

    new_item = FEED.replace("wfn-1001", "wfn-2001").replace("Hulk Hogan", "Iron Sheik") \
                   .replace("Hart Foundation Tag Team 2-Pack", "Brother Love").replace("Jey Uso", "Jake Roberts")
    st["feeds"] = {}
    f2, _ = fetcher(cfg, st, body=new_item)
    tg2 = FakeTG()
    run(cfg, st, f2, tg2, now=1_000_700)
    texts = [t for _, t in tg2.sent]
    assert len(texts) == 3
    assert any("Iron Sheik" in t and "$26.99" in t and "goto.target.com" in t and "Target" in t for t in texts)


def test_duplicates_purchases_pause_and_budget():
    cfg = one_feed_cfg()
    st = default_state(cfg)
    st["seeded"] = True
    st["telegram"]["chat_id"] = 42
    st["purchases"].append({"figure_id": "cm-punk", "name": "CM Punk", "price": 200, "qty": 1,
                            "retailer": "wweshop", "ts": 1_000_000})
    st["monthly_budget"] = 100
    f, _ = fetcher(cfg, st)
    tg = FakeTG()
    run(cfg, st, f, tg, now=1_000_000)
    texts = [t for _, t in tg.sent]
    assert not any("CM Punk" in t and "BACK IN STOCK" in t for t in texts)   # already bought
    assert any("already bought" in e["text"] for e in st["events"])
    assert any("over this month" in t for t in texts if "PREORDER" in t)   # budget warning
    # same preorder reported again by another site within 24h -> no repeat alert
    dup = FEED.replace("wfn-100", "other-site-")
    st["feeds"] = {}
    f2, _ = fetcher(cfg, st, body=dup)
    tg2 = FakeTG()
    run(cfg, st, f2, tg2, now=1_000_900)
    assert not any("PREORDER" in t for _, t in tg2.sent)
    # emergency stop holds everything
    st["paused"] = True
    st["feeds"] = {}
    f3, _ = fetcher(cfg, st, body=FEED.replace("wfn-100", "third-"))
    tg3 = FakeTG()
    run(cfg, st, f3, tg3, now=1_000_000 + 2 * 86400)
    assert tg3.sent == []


def test_hard_stop_env_blocks_alerts():
    cfg = one_feed_cfg()
    st = default_state(cfg)
    st["seeded"] = True
    st["telegram"]["chat_id"] = 1
    f, _ = fetcher(cfg, st)
    tg = FakeTG()
    run(cfg, st, f, tg, now=1_000_000, hard_stop=True)
    assert tg.sent == []


# ---------------- politeness ----------------
def test_never_fetches_retailers_and_respects_robots():
    cfg = one_feed_cfg("https://www.target.com/feed/")
    st = default_state(cfg)
    f, sess = fetcher(cfg, st)
    assert f.fetch(cfg["feeds"][0])[0] is None and sess.calls == []

    cfg = one_feed_cfg()
    st = default_state(cfg)
    f, sess = fetcher(cfg, st, robots="User-agent: *\nDisallow: /feed/\n")
    text, why = f.fetch(cfg["feeds"][0])
    assert text is None and "robots" in why
    assert not any("/feed/" in c and "robots" not in c for c in sess.calls)


def test_rate_limit_backoff_and_min_gap():
    cfg = one_feed_cfg()
    st = default_state(cfg)
    sess = FakeSession({"robots.txt": Resp(200, ""), "/feed/": Resp(429, "", {"Retry-After": "900"})})
    now = [1_000_000]
    f = Fetcher(cfg, st, session=sess, now=lambda: now[0])
    assert f.fetch(cfg["feeds"][0])[0] is None
    n = len(sess.calls)
    now[0] += 300
    assert f.fetch(cfg["feeds"][0])[1].startswith("waiting") and len(sess.calls) == n
    now[0] += 700
    sess.routes["/feed/"] = Resp(200, FEED)
    assert f.fetch(cfg["feeds"][0])[0]
    now[0] += 60  # too soon after a good fetch
    assert f.fetch(cfg["feeds"][0])[1].startswith("waiting")


# ---------------- telegram commands ----------------
def test_commands_link_ignore_strangers_and_log_purchases():
    st = default_state(CFG)
    upsert_figure(st, "Hulk Hogan")
    tg = FakeTG([msg(1, 7, "hi"), msg(2, 7, "/start"), msg(3, 99, "/pause"),
                 msg(4, 7, "/bought hogan 26.99 target"), msg(5, 7, "/budget 20")])
    replies = commands.handle_updates(st, tg)
    assert st["telegram"]["chat_id"] == 7 and st["telegram"]["offset"] == 6
    assert st["paused"] is False                       # stranger's /pause ignored
    assert st["purchases"][0]["figure_id"] == "hulk-hogan"
    assert st["purchases"][0]["price"] == 26.99 and st["monthly_budget"] == 20
    assert all(c == 7 for c, _ in replies)
    assert "Over your monthly budget" not in replies[1][1]
    assert "Over your monthly budget" in commands.run_command(st, "/bought jey uso 24.99")
    assert "Paused" in commands.run_command(st, "/stop") and st["paused"]
    assert "back on" in commands.run_command(st, "/resume") and not st["paused"]


def test_dashboard_escapes_and_renders(tmp_path):
    st = default_state(CFG)
    fid, fig = upsert_figure(st, "</script><b>x</b>")
    fig["retailers"]["target"] = {"status": "preorder", "url": "javascript:alert(1)", "price": 9.0}
    out = tmp_path / "index.html"
    dashboard.write(st, CFG, str(out))
    html = out.read_text()
    assert "</script><b>" not in html
    assert html.count("<script>") == 1
    assert "safe(r.url)" in html  # non-http links neutralised in the browser
