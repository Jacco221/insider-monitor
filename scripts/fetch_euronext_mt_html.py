#!/usr/bin/env python3
# Euronext corporate announcements (HTML) → data/reports/eu_events.jsonl (append)
# Doel: ruimer vangen van "Managers’ Transactions" / PDMR / insider updates.
import os, re, json, pathlib, sys
from urllib.request import urlopen, Request
from html import unescape

OUT = pathlib.Path("data/reports/eu_events.jsonl"); OUT.parent.mkdir(parents=True, exist_ok=True)
UA  = os.getenv("EURONEXT_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")

# Vul hier de markten die je wilt volgen. Dit zijn corporate news pagina's (HTML).
# Je kunt later specifieke tickers toevoegen (company pages) – parser blijft werken.
FEED_URLS = [
    "https://live.euronext.com/en/markets/amsterdam/corporate-news",
    "https://live.euronext.com/en/markets/paris/corporate-news",
    "https://live.euronext.com/en/markets/brussels/corporate-news",
    "https://live.euronext.com/en/markets/lisbon/corporate-news",
    "https://live.euronext.com/en/markets/dublin/corporate-news",
]

# Ruime set aan sleutelwoorden (NL/EN). Voel je vrij om uit te breiden.
KEEP_RX = re.compile(r"(?i)(manager.?s'? transactions?|pdmr|director|insider|bestuurder|leidinggevend)")
# Bekende ruis uitsluiten
DROP_RX = re.compile(r"(?i)\b(fund|fonds|obligatie|bond|certificate|structured|note|warrant|etf)\b")

# Eenvoudige HTML fetch (zonder externe libs)
def fetch(url, timeout=20):
    try:
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"[euronext-html] fetch error {url}: {e}", file=sys.stderr)
        return ""

def extract_items(html: str):
    """
    Euronext markup varieert. We zoeken generiek naar anchor/blokken die op news-items lijken.
    We pakken (title, href) en proberen een datum/uur te vangen uit de block-tekst.
    """
    items = []
    if not html: 
        return items

    # 1) Vaak bestaan nieuwsregels uit <a ...>Title</a> binnen cards; pak anchors + context
    #   zoek anchor + stukje context-tekst (voor datum).
    for m in re.finditer(r'(<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>)', html, flags=re.S|re.I):
        a_tag, href, inner = m.group(1), m.group(2), m.group(3)
        title = unescape(re.sub(r"<.*?>"," ", inner)).strip()
        if not title: 
            continue
        # pak 150 chars context na anchor (kan datum bevatten)
        after = html[m.end(): m.end()+200]
        ctx = unescape(re.sub(r"<.*?>"," ", after)).strip()
        items.append((title, href, ctx))

    # Dedup op (title, href)
    seen=set(); out=[]
    for t, h, c in items:
        key=(t.strip(), h.strip())
        if key in seen: 
            continue
        seen.add(key); out.append((t.strip(), h.strip(), c.strip()))
    return out

def normalize(title, href, ctx):
    t = title.strip()
    if not t: 
        return None, "empty-title"
    if not KEEP_RX.search(t): 
        return None, "no-keyword"
    if DROP_RX.search(t): 
        return None, "drop-ruis"

    # absolute URL
    if href and not href.startswith("http"):
        href = "https://live.euronext.com" + href

    # issuer heuristisch: deel vóór " - " of eerste 8–12 woorden
    issuer = t.split(" - ")[0].strip()
    if len(issuer) < 2:
        issuer = t.split()[:8]
        issuer = " ".join(issuer)

    ev = {
        "source":"Euronext",
        "xml_url": href,
        "ticker": issuer.upper(),
        "issuer": issuer,
        "owner": "Insider",
        "title": "Managers' Transactions",
        "txs": [],
        "when": ctx or "",
    }
    return ev, None

def main():
    total=kept=0
    dropped={"empty-title":0, "no-keyword":0, "drop-ruis":0}
    samples=[]
    evts=[]
    for url in FEED_URLS:
        html = fetch(url)
        if not html:
            continue
        items = extract_items(html)
        total += len(items)
        for (title, href, ctx) in items:
            if len(samples) < 5:
                samples.append(title[:120])
            ev, reason = normalize(title, href, ctx)
            if ev:
                kept += 1
                evts.append(ev)
            else:
                if reason in dropped: 
                    dropped[reason] += 1

    print(f"[euronext-html] items={total} kept={kept} dropped={dropped} samples={samples}")
    if not evts:
        print("[euronext-html] no candidates")
        return 0

    with OUT.open("a", encoding="utf-8") as f:
        for e in evts:
            f.write(json.dumps(e)+"\n")
    print(f"[euronext-html] wrote {len(evts)} events -> {OUT}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
