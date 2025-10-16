#!/usr/bin/env python3
# Euronext corporate announcements (Managers’ Transactions) → data/reports/eu_events.jsonl (append)
import os, re, json, pathlib
from urllib.request import urlopen, Request
from html import unescape

OUT = pathlib.Path("data/reports/eu_events.jsonl"); OUT.parent.mkdir(parents=True, exist_ok=True)
UA  = os.getenv("EURONEXT_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")

# Vervang door markt-/ticker-specifieke RSS zodra bekend; dit is een brede feed als voorbeeld.
FEEDS = [
    "https://live.euronext.com/en/rss-feed",
]

KEYS = re.compile(r"(?i)\b(manager|leidinggevend|pdmr).*?(transact|transaction|deal|meld)", re.S)
BAD  = re.compile(r"(?i)\b(fund|obligatie|bond|note|certificate|structured)\b")

def fetch(url, timeout=15):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")

def parse_rss(xml):
    items = re.findall(r"<item>(.+?)</item>", xml, flags=re.S|re.I)
    out=[]
    for it in items:
        title = unescape(re.sub(r"<.*?>","", re.search(r"<title>(.*?)</title>", it, flags=re.S|re.I).group(1))).strip()
        linkm = re.search(r"<link>(.*?)</link>", it, flags=re.S|re.I)
        link  = unescape(linkm.group(1)).strip() if linkm else ""
        pubm  = re.search(r"<pubDate>(.*?)</pubDate>", it, flags=re.S|re.I)
        when  = pubm.group(1).strip() if pubm else ""
        out.append((title, link, when))
    return out

def normalize(title, link, when):
    if BAD.search(title) or not KEYS.search(title):
        return None
    issuer = unescape(title.split(" - ")[0]).strip()
    return {
        "source":"Euronext",
        "xml_url": link,
        "ticker": issuer.upper(),
        "issuer": issuer,
        "owner": "Insider",
        "title": "Managers' Transactions",
        "txs": [],
        "when": when
    }

def main():
    evts=[]
    for u in FEEDS:
        try:
            xml = fetch(u)
            for title, link, when in parse_rss(xml):
                ev = normalize(title, link, when)
                if ev: evts.append(ev)
        except Exception:
            continue
    if not evts:
        print("[euronext] no candidates"); return 0
    with OUT.open("a", encoding="utf-8") as f:
        for e in evts: f.write(json.dumps(e)+"\n")
    print(f"[euronext] wrote {len(evts)} events -> {OUT}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
