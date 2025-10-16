#!/usr/bin/env python3
# Euronext corporate announcements (Managers’ Transactions) → data/reports/eu_events.jsonl (append)
import os, re, json, pathlib, sys
from urllib.request import urlopen, Request
from html import unescape

OUT = pathlib.Path("data/reports/eu_events.jsonl"); OUT.parent.mkdir(parents=True, exist_ok=True)
UA  = os.getenv("EURONEXT_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")

# Brede feed als voorbeeld; voor productie kun je specifieke markt-/ticker-feeds configureren.
FEEDS = [
    "https://live.euronext.com/en/rss-feed",
]

# Ruime set aan sleutelwoorden (NL/EN/FR varianten voorkomen we hier; dit is basaal)
KEEP_RX = re.compile(r"(?i)\b(managers?'?\s*transactions?|leidinggevend|bestuurder|pdmr|insider)\b")
DROP_RX = re.compile(r"(?i)\b(fund|fonds|obligatie|bond|certificate|structured|note|warrant|etf)\b")

def fetch(url, timeout=20):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")

def parse_rss(xml):
    items = re.findall(r"<item>(.+?)</item>", xml, flags=re.S|re.I)
    out=[]
    for it in items:
        try:
            title = unescape(re.sub(r"<.*?>","", re.search(r"<title>(.*?)</title>", it, flags=re.S|re.I).group(1))).strip()
        except Exception:
            continue
        linkm = re.search(r"<link>(.*?)</link>", it, flags=re.S|re.I)
        link  = unescape(linkm.group(1)).strip() if linkm else ""
        pubm  = re.search(r"<pubDate>(.*?)</pubDate>", it, flags=re.S|re.I)
        when  = pubm.group(1).strip() if pubm else ""
        out.append((title, link, when))
    return out

def normalize(title, link, when):
    t = title.strip()
    if not KEEP_RX.search(t):
        return None, "no-keyword"
    if DROP_RX.search(t):
        return None, "drop-ruis"
    issuer = unescape(t.split(" - ")[0]).strip()
    ev = {
        "source":"Euronext",
        "xml_url": link,
        "ticker": issuer.upper(),
        "issuer": issuer,
        "owner": "Insider",
        "title": "Managers' Transactions",
        "txs": [],
        "when": when
    }
    return ev, None

def main():
    total=kept=0
    dropped={"no-keyword":0, "drop-ruis":0}
    sample_seen=[]
    evts=[]
    for u in FEEDS:
        try:
            xml = fetch(u)
            items = parse_rss(xml)
            total += len(items)
            for (title, link, when) in items:
                if len(sample_seen)<5: sample_seen.append(title.strip())
                ev, reason = normalize(title, link, when)
                if ev:
                    kept += 1
                    evts.append(ev)
                else:
                    if reason in dropped: dropped[reason]+=1
        except Exception as e:
            print(f"[euronext] fetch error {u}: {e}", file=sys.stderr)
            continue

    print(f"[euronext] items={total} kept={kept} dropped={dropped} samples={sample_seen}")
    if not evts:
        print("[euronext] no candidates")
        return 0

    with OUT.open("a", encoding="utf-8") as f:
        for e in evts:
            f.write(json.dumps(e)+"\n")
    print(f"[euronext] wrote {len(evts)} events -> {OUT}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
