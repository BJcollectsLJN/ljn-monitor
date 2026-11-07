"""Minimal Telegram Bot API client. The token comes only from the environment
and is never printed or saved."""
import html
import os
import time

import requests

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token=None, session=None):
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.s = session or requests.Session()

    @property
    def enabled(self):
        return bool(self.token)

    def _call(self, method, **params):
        if not self.enabled:
            return None
        for attempt in range(3):
            try:
                r = self.s.post(API.format(token=self.token, method=method), json=params, timeout=30)
                data = r.json()
            except (requests.RequestException, ValueError) as e:
                print(f"[telegram] {method} failed: {type(e).__name__}")  # no token in output
                time.sleep(2 * (attempt + 1))
                continue
            if data.get("ok"):
                return data["result"]
            retry = (data.get("parameters") or {}).get("retry_after")
            if retry and attempt < 2:
                time.sleep(min(int(retry), 30))
                continue
            print(f"[telegram] {method} error: {data.get('description')}")
            return None
        return None

    def send(self, chat_id, text):
        if not chat_id:
            return None
        return self._call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                          disable_web_page_preview=False)

    def updates(self, offset):
        return self._call("getUpdates", offset=offset, timeout=0,
                          allowed_updates=["message"]) or []


def esc(s):
    return html.escape(str(s), quote=True)
