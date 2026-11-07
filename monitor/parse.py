"""Turn RSS items into structured LJN figure events."""
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .sources import host_matches

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
}

RETAILER_HOSTS = {
    "target": ["target.com"],
    "wweshop": ["wweshop.com"],
}

GENERIC_ANCHORS = {
    "here", "click here", "link", "target", "wwe shop", "wweshop", "shop", "buy",
    "buy now", "order", "order here", "order now", "pre-order", "preorder",
    "pre-order here", "preorder here", "pre-order now", "available here", "target.com",
    "wweshop.com", "check it out", "shop now", "this link", "at target", "on target",
}

MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATE_RE = re.compile(
    r"(?:releas\w*|ships?|shipping|arriv\w*|due|expected|available|street date)[^.$]{0,40}?"
    r"((?:" + MONTHS + r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)|(?:" + MONTHS
    + r"\s+\d{4})|(?:(?:early|mid|late|spring|summer|fall|winter)\s+\d{4})|(?:Q[1-4]\s+\d{4}))",
    re.I)
PRICE_RE = re.compile(r"\$\s?(\d{1,4}(?:\.\d{2})?)")

EVENT_PATTERNS = [
    ("restock", r"\bre-?stock|back in stock|available again|returns? to stock"),
    ("preorder", r"\bpre-?orders?\b"),
    ("sold_out", r"\bsold out\b|\bsells out\b"),
    ("in_stock", r"\bin stock\b|now available|available now|\bon shelves\b|\bnow live\b|\bout now\b"),
    ("announced", r"\breveal|\bannounc|\bfirst look|\bofficial images|\bunveil|\bsdcc\b|comic-?con"),
]

NAME_STRIP = re.compile(
    r"\b(?:wwe|ljn|superstars?|action figures?|figures?|mattel|exclusive|series\s*\d+|"
    r"wave\s*\d+|pre-?orders?|new)\b", re.I)


def classify(text):
    t = text.lower()
    for ev, pat in EVENT_PATTERNS:
        if re.search(pat, t):
            return ev
    return "unknown"


def retailer_for(url):
    host = urlparse(url).netloc.lower()
    for r, hosts in RETAILER_HOSTS.items():
        if host_matches(host, hosts):
            return r
    if "wweshop" in url.lower():  # affiliate redirect links
        return "wweshop"
    return None


def clean_name(text):
    t = re.sub(r"\(.*?\)", " ", text)
    t = PRICE_RE.sub(" ", t)
    t = NAME_STRIP.sub(" ", t)
    t = re.sub(r"[‘’]", "'", t)
    t = re.sub(r"[“”]", '"', t)
    t = re.sub(r"\s+", " ", t).strip(" -–—:|,.;\"'")
    return t if 2 < len(t) <= 60 else None


def parse_feed(xml_text):
    """Return list of {guid,title,link,published,html} from RSS 2.0 or Atom."""
    xml_text = xml_text.lstrip("﻿ \r\n\t")
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        html = it.findtext("content:encoded", default="", namespaces=NS) or it.findtext("description", default="")
        link = (it.findtext("link") or "").strip()
        items.append({
            "guid": (it.findtext("guid") or link or it.findtext("title") or "").strip(),
            "title": (it.findtext("title") or "").strip(),
            "link": link,
            "published": (it.findtext("pubDate") or "").strip(),
            "html": html,
        })
    for e in root.iter("{%s}entry" % NS["atom"]):
        link_el = e.find("atom:link", NS)
        link = link_el.get("href") if link_el is not None else ""
        html = e.findtext("atom:content", default="", namespaces=NS) or e.findtext("atom:summary", default="", namespaces=NS)
        items.append({
            "guid": (e.findtext("atom:id", default="", namespaces=NS) or link).strip(),
            "title": (e.findtext("atom:title", default="", namespaces=NS) or "").strip(),
            "link": link,
            "published": (e.findtext("atom:updated", default="", namespaces=NS) or "").strip(),
            "html": html,
        })
    return items


def is_ljn(item, keywords):
    blob = (item["title"] + " " + BeautifulSoup(item["html"] or "", "html.parser").get_text(" ")).lower()
    return any(k.lower() in blob for k in keywords)


def extract(item):
    """Find figure listings (name, retailer, link, price, release) in one article."""
    soup = BeautifulSoup(item["html"] or "", "html.parser")
    item_event = classify(item["title"]) if classify(item["title"]) != "unknown" else classify(soup.get_text(" ")[:600])
    listings, seen = [], set()
    for a in soup.find_all("a", href=True):
        url = a["href"].strip()
        retailer = retailer_for(url)
        if not retailer:
            continue
        block = a.find_parent(["li", "p", "td", "h2", "h3", "h4", "div"]) or a
        block_text = block.get_text(" ", strip=True)
        anchor = a.get_text(" ", strip=True)
        name = None
        if anchor and anchor.lower().strip(" :!.") not in GENERIC_ANCHORS:
            name = clean_name(anchor)
        if not name:
            rest = block_text.replace(anchor, " ") if anchor else block_text
            rest = re.split(r"[:–—|]|\s-\s|\$", rest)[0]
            name = clean_name(rest)
        if not name:
            continue
        price = PRICE_RE.search(block_text)
        date = DATE_RE.search(block_text) or DATE_RE.search(soup.get_text(" "))
        ev = classify(block_text)
        key = (name.lower(), retailer)
        if key in seen:
            continue
        seen.add(key)
        listings.append({
            "name": name,
            "retailer": retailer,
            "url": url,
            "price": float(price.group(1)) if price else None,
            "release": date.group(1).strip() if date else None,
            "event": ev if ev not in ("unknown", "announced") else item_event,
        })
    return {"event": item_event, "listings": listings}
