"""Polite RSS fetching: robots.txt, conditional requests, rate limits, backoff.

Retailer sites (Target, WWEShop) are never fetched; their terms prohibit bots.
"""
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

ROBOTS_TTL = 24 * 3600


def host_matches(host, domains):
    host = (host or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


class Fetcher:
    def __init__(self, cfg, state, session=None, now=None):
        self.cfg = cfg
        self.st = state
        self.ua = cfg["user_agent"]
        self.timeout = cfg.get("request_timeout_seconds", 20)
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = self.ua
        self.now = now or time.time

    # --- robots.txt -------------------------------------------------------
    def _robots(self, url):
        p = urlparse(url)
        host = p.netloc.lower()
        cache = self.st["robots"].get(host)
        if not cache or self.now() - cache["ts"] > ROBOTS_TTL:
            robots_url = f"{p.scheme}://{host}/robots.txt"
            try:
                r = self.s.get(robots_url, timeout=self.timeout)
                if r.status_code in (401, 403):
                    cache = {"ts": self.now(), "mode": "deny", "text": ""}
                elif r.status_code >= 400:
                    cache = {"ts": self.now(), "mode": "allow", "text": ""}
                else:
                    cache = {"ts": self.now(), "mode": "rules", "text": r.text[:50000]}
            except requests.RequestException:
                return None  # unknown -> don't fetch this round
            self.st["robots"][host] = cache
        return cache

    def allowed(self, url):
        host = urlparse(url).netloc
        if host_matches(host, self.cfg["never_fetch_hosts"]):
            return False, "retailer site (bots prohibited)"
        cache = self._robots(url)
        if cache is None:
            return False, "robots.txt unreachable"
        if cache["mode"] == "deny":
            return False, "robots.txt access denied"
        if cache["mode"] == "allow":
            return True, ""
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(cache["text"].splitlines())
        if not rp.can_fetch(self.ua, url):
            return False, "disallowed by robots.txt"
        return True, ""

    # --- feed fetch -------------------------------------------------------
    def fetch(self, feed):
        url = feed["url"]
        fs = self.st["feeds"].setdefault(url, {"fails": 0, "next_ok": 0})
        now = self.now()
        if now < fs.get("next_ok", 0):
            return None, "waiting (rate limit/backoff)"
        ok, why = self.allowed(url)
        if not ok:
            fs["status"] = why
            fs["next_ok"] = now + 6 * 3600
            return None, why

        headers = {}
        if fs.get("etag"):
            headers["If-None-Match"] = fs["etag"]
        if fs.get("last_modified"):
            headers["If-Modified-Since"] = fs["last_modified"]
        min_gap = self.cfg.get("min_minutes_between_feed_fetches", 9) * 60
        try:
            r = self.s.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            return None, self._backoff(fs, f"network error: {type(e).__name__}")

        fs["last_fetch"] = int(now)
        if r.status_code == 304:
            fs.update(fails=0, next_ok=now + min_gap, status="ok (unchanged)")
            return None, "unchanged"
        if r.status_code in (429, 503):
            wait = _retry_after(r.headers.get("Retry-After"))
            return None, self._backoff(fs, f"rate limited ({r.status_code})", wait)
        if r.status_code >= 400:
            return None, self._backoff(fs, f"HTTP {r.status_code}")

        fs.update(fails=0, next_ok=now + min_gap, status="ok",
                  etag=r.headers.get("ETag"), last_modified=r.headers.get("Last-Modified"))
        return r.text, "ok"

    def _backoff(self, fs, why, wait=None):
        fs["fails"] = fs.get("fails", 0) + 1
        wait = wait or min(6 * 3600, 600 * (2 ** (fs["fails"] - 1)))
        fs["next_ok"] = self.now() + wait
        fs["status"] = why
        return why


def _retry_after(v):
    try:
        return max(60, min(int(v), 24 * 3600))
    except (TypeError, ValueError):
        return None
