"""Fetchers for every lead source. Each fetcher returns a list of raw lead dicts."""
import base64
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

UA = "Mozilla/5.0 (compatible; LeadRadar/1.0; personal lead finder)"
ATOM = "{http://www.w3.org/2005/Atom}"
HN = "https://hn.algolia.com/api/v1"


def fetch(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise
        time.sleep(3)  # one retry for flaky connections
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_date(value):
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def make_lead(source, kind, title, body, url, author, posted):
    return {
        "id": hashlib.sha1(url.encode()).hexdigest()[:16],
        "source": source,
        "kind": kind,  # "social" = people posting, "jobboard" = listings (hiring is implied)
        "title": clean(title)[:200],
        "body": clean(body)[:3000],
        "url": url,
        "author": author or "",
        "posted": posted.isoformat(),
    }


def unwrap_google(url):
    parts = urllib.parse.urlparse(url)
    if "google." in parts.netloc and parts.path == "/url":
        real = urllib.parse.parse_qs(parts.query).get("url")
        if real:
            return real[0]
    return url


def _text(node):
    return node.text if node is not None and node.text else ""


def parse_feed(raw, source, kind):
    root = ET.fromstring(raw)
    leads = []
    if root.tag == ATOM + "feed":
        for e in root.findall(ATOM + "entry"):
            link = ""
            for l in e.findall(ATOM + "link"):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            if not link:
                continue
            body = _text(e.find(ATOM + "content")) or _text(e.find(ATOM + "summary"))
            when = _text(e.find(ATOM + "updated")) or _text(e.find(ATOM + "published"))
            author = _text(e.find(ATOM + "author/" + ATOM + "name"))
            leads.append(make_lead(source, kind, _text(e.find(ATOM + "title")), body,
                                   unwrap_google(link), author, parse_date(when)))
    else:
        for item in root.iter("item"):
            link = _text(item.find("link")).strip()
            if not link:
                continue
            leads.append(make_lead(source, kind, _text(item.find("title")), _text(item.find("description")),
                                   link, "", parse_date(_text(item.find("pubDate")))))
    return leads


def _reddit_token():
    """App-only OAuth token, used when REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set."""
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        return None
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token", data=b"grant_type=client_credentials",
        headers={"User-Agent": UA, "Authorization": f"Basic {basic}"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read())["access_token"]


def reddit(cfg):
    token = _reddit_token()
    leads = []
    for i, sub in enumerate(cfg["reddit_subs"]):
        if i:
            time.sleep(2)  # be polite
        try:
            if token:
                data = json.loads(fetch(f"https://oauth.reddit.com/r/{sub}/new?limit=50",
                                        {"Authorization": f"Bearer {token}"}))
                for child in data["data"]["children"]:
                    d = child["data"]
                    leads.append(make_lead(f"r/{sub}", "social", d["title"], d.get("selftext", ""),
                                           "https://www.reddit.com" + d["permalink"], d.get("author"),
                                           datetime.fromtimestamp(d["created_utc"], timezone.utc)))
            else:
                leads += parse_feed(fetch(f"https://www.reddit.com/r/{sub}/new/.rss?limit=50"), f"r/{sub}", "social")
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise RuntimeError(f"Reddit refused requests ({exc.code}); set REDDIT_CLIENT_ID/SECRET to use the official API")
            raise
    return leads


def rss_feeds(cfg):
    leads = []
    for feed in cfg["rss_feeds"]:
        leads += parse_feed(fetch(feed["url"]), feed["name"], "jobboard")
    return leads


def google_alerts(cfg):
    leads = []
    for i, url in enumerate(cfg["google_alert_feeds"], 1):
        leads += parse_feed(fetch(url), f"Google Alert {i}", "social")
    return leads


def remoteok(cfg):
    items = json.loads(fetch("https://remoteok.com/api"))
    leads = []
    for it in items:
        if not isinstance(it, dict) or not it.get("url"):
            continue
        title = f"{it.get('position', '')} @ {it.get('company', '')}"
        body = f"{it.get('description', '')} {' '.join(it.get('tags') or [])}"
        leads.append(make_lead("RemoteOK", "jobboard", title, body, it["url"],
                               it.get("company", ""), parse_date(it.get("date"))))
    return leads


def _hn_url(object_id):
    return f"https://news.ycombinator.com/item?id={object_id}"


def hacker_news(cfg):
    since = int(time.time() - cfg["max_age_hours"] * 3600)
    leads = []
    for q in cfg["hn_queries"]:
        params = urllib.parse.urlencode({
            "query": q, "tags": "(story,comment)", "hitsPerPage": 50,
            "numericFilters": f"created_at_i>{since}",
        }, safe="(),>")
        for h in json.loads(fetch(f"{HN}/search_by_date?{params}"))["hits"]:
            title = h.get("title") or f"HN comment on: {h.get('story_title') or ''}"
            body = h.get("comment_text") or h.get("story_text") or ""
            leads.append(make_lead("Hacker News", "social", title, body, _hn_url(h["objectID"]),
                                   h.get("author"), datetime.fromtimestamp(h["created_at_i"], timezone.utc)))
    return leads


def hn_threads(cfg):
    """Monthly 'Who is hiring?' and 'Freelancer? Seeking freelancer?' threads."""
    params = urllib.parse.urlencode({"query": "Ask HN", "tags": "story,author_whoishiring", "hitsPerPage": 8})
    threads = [h for h in json.loads(fetch(f"{HN}/search_by_date?{params}"))["hits"]
               if re.search(r"who is hiring|seeking freelancer", h.get("title") or "", re.I)][:2]
    since = time.time() - cfg["max_age_hours"] * 3600
    leads = []
    for t in threads:
        tree = json.loads(fetch(f"{HN}/items/{t['objectID']}", timeout=60))
        for c in tree.get("children", []):
            if not c.get("text") or (c.get("created_at_i") or 0) < since:
                continue
            body = clean(c["text"])
            leads.append(make_lead("HN Who's Hiring", "jobboard", body[:120], body, _hn_url(c["id"]),
                                   c.get("author"), datetime.fromtimestamp(c["created_at_i"], timezone.utc)))
    return leads


SOURCES = [
    ("Reddit", reddit),
    ("RSS job boards", rss_feeds),
    ("RemoteOK", remoteok),
    ("Hacker News", hacker_news),
    ("HN threads", hn_threads),
    ("Google Alerts", google_alerts),
]
