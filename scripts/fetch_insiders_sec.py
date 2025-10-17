#!/usr/bin/env python3
# SEC Form 4 – robuuste fetcher:
# - kiest beste XML uit alle links op de indexpagina (form4/ownership/primary)
# - namespace-aware parsing (nonDerivative + derivative)
# - schrijft zowel sec_headlines.txt als sec_events.jsonl
# - optionele diagnose: SEC_DIAG=1 => print P/S/M/F counts voor eerste filings

import os, re, json, html, time, pathlib, sys
from urllib.request import Request, urlopen
from urllib.parse import urljoin

BASE = pathlib.Path("data")
REPORTS = BASE / "reports"; REPORTS.mkdir(parents=True, exist_ok=True)
STATE   = BASE / "state";   STATE.mkdir(parents=True, exist_ok=True)
SEEN    = STATE / "sec_seen.jsonl"
OUT_TXT = REPORTS / "sec_headlines.txt"
OUT_EVT = REPORTS / "sec_events.jsonl"

ATOM_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&count=200&output=atom"
UA = os.getenv("SEC_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")
DIAG = bool(os.getenv("SEC_DIAG"))

def fetch(url: str, timeout=20, retries=2) -> str:
    for i in range(retries+1):
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:
            time.sleep(1 + i)
    return ""

def parse_atom(xml: str):
    out=[]
    for block in re.findall(r"<entry>(.+?)</entry>", xml, flags=re.S|re.I):
        def tag(n,a=None):
            if a:
                m=re.search(fr"<{n}[^>]*{a}=\"([^\"]+)\"[^>]*/?>", block, flags=re.I)
                return m.group(1) if m else ""
            m=re.search(fr"<{n}[^>]*>(.*?)</{n}>", block, flags=re.S|re.I)
            return m.group(1).strip() if m else ""
        title   = html.unescape(re.sub(r"<.*?>","", tag("title"))).strip()
        link    = tag("link","href") or ""
        updated = tag("updated") or ""
        out.append({"title":title,"link":link,"updated":updated})
    return out

def find_xml_candidates(idx_url: str):
    """Geef lijst (url, score) met alle .xml-links; scoreert op naam."""
    page = fetch(idx_url)
    cands = []
    for m in re.finditer(r'href="([^"]+\.(?:xml|XML))"', page):
        href = html.unescape(m.group(1))
        url  = href if href.startswith("http") else urljoin("https://www.sec.gov", href)
        name = url.lower()
        score = 0
        # voorkeuren: form4/ownership/primary/... (wk-form4_...xml is vaak de juiste)
        if "form4"     in name: score += 5
        if "ownership" in name: score += 4
        if "primary"   in name: score += 3
        if "xml"       in name: score += 1
        cands.append((url, score))
    # als niks gevonden, return lege lijst
    cands.sort(key=lambda x: x[1], reverse=True)
    return cands

# ===== XML helpers (namespace-aware) =====
def g(xml: str, tag: str) -> str:
    m = re.search(fr"<(?:\w+:)?{tag}\b[^>]*>(.*?)</(?:\w+:)?{tag}>", xml, flags=re.S|re.I)
    return html.unescape((m.group(1) if m else "").strip())

def gv(xml: str, tag: str) -> str:
    m = re.search(fr"<(?:\w+:)?{tag}\b[^>]*>\s*(?:<(?:\w+:)?value[^>]*>)?\s*([^<]+)", xml, flags=re.S|re.I)
    return html.unescape((m.group(1) if m else "").strip())

def blks(xml: str, tag: str):
    return re.findall(fr"<(?:\w+:)?{tag}\b[^>]*>(.+?)</(?:\w+:)?{tag}>", xml, flags=re.S|re.I)

def parse_form4(xml: str):
    owner  = g(xml,"rptOwnerName")
    title  = g(xml,"officerTitle")
    ticker = g(xml,"issuerTradingSymbol").upper()
    issuer = g(xml,"issuerName")
    txs=[]
    for blk in blks(xml,"nonDerivativeTransaction") + blks(xml,"derivativeTransaction"):
        code = g(blk,"transactionCode").upper()
        ad   = gv(blk,"transactionAcquiredDisposedCode").upper()
        sh   = gv(blk,"transactionShares")
        pr   = (gv(blk,"transactionPricePerShare") or
                gv(blk,"conversionOrExercisePrice") or
                gv(blk,"exercisePrice"))
        tv   = gv(blk,"transactionTotalValue")
        try: shf = float((sh or "0").replace(",",""))
        except: shf = 0.0
        price = 0.0
        if pr:
            try: price = float((pr or "0").replace(",",""))
            except: price = 0.0
        elif tv and shf>0:
            try: price = float((tv or "0").replace(",",""))/shf
            except: price = 0.0
        total = shf*price
        txs.append({"code":code,"ad":ad,"shares":shf,"price":price,"total":total})
    return {"ticker":ticker,"issuer":issuer,"owner":owner,"title":title,"txs":txs}

def summarize(txs):
    def s(sel): return sum(t["total"] for t in txs if sel(t))
    buy  = s(lambda t: t["code"]=="P" or t["ad"]=="A")
    sell = s(lambda t: t["code"]=="S" or t["ad"]=="D")
    m    = s(lambda t: t["code"]=="M")
    f    = s(lambda t: t["code"]=="F")
    return buy,sell,m,f

def human(v):
    if v>=1_000_000: return f"${v/1_000_000:.1f}M"
    if v>=1_000:     return f"${v/1_000:.0f}k"
    return f"${v:.0f}"

def build_line(ev, updated, atom_title):
    when = (updated or "").replace("T"," ").replace("Z"," UTC")
    base = (ev.get("ticker") or ev.get("issuer") or (atom_title or "").split(" (")[0].strip() or "UNKNOWN").upper()
    who  = ev.get("owner") or "Insider"
    buy,sell,m,f = summarize(ev.get("txs") or [])
    parts=[]
    if buy:  parts.append(f"BUY {human(buy)}")
    if sell: parts.append(f"SELL {human(sell)}")
    if m:    parts.append(f"M {human(m)}")
    if f:    parts.append(f"F {human(f)}")
    if parts:
        role = "CEO/CFO" if any(k in (ev.get("title","").lower()) for k in ["chief executive","ceo","chief financial","cfo","president","chair"]) else \
               ("Officer/Dir" if ev.get("title") else "Insider")
        return f"- [SEC] {base} – {who} ({role}): " + ", ".join(parts) + f" ({when})"
    return f"- [SEC] {base} – {who}: Form 4 filed ({when})"

def load_seen():
    seen=set()
    if SEEN.exists():
        for line in SEEN.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            try:
                j=json.loads(line); k=j.get("key")
                if k: seen.add(k)
            except: pass
    return seen

def append_seen(keys):
    if not keys: return
    with SEEN.open("a", encoding="utf-8") as f:
        for k in keys:
            f.write(json.dumps({"key":k,"ts":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})+"\n")

def main():
    atom = fetch(ATOM_URL)
    entries = parse_atom(atom)
    # pak alleen de 160 meest recente
    entries = entries[:160]
    seen = load_seen()
    new_keys=[]; lines=[]; evts=[]
    diag_left = 3 if DIAG else 0

    for e in entries:
        link = e.get("link",""); updated = e.get("updated",""); title = e.get("title","")
        if not link: continue
        # Bepaal accession (laatste path-component zonder -index.htm)
        acc = None
        m = re.search(r'/([\d-]+)-index\.htm', link)
        if m: acc = m.group(1)
        key = acc or (link + "|" + updated)
        if key in seen:
            continue

        # zoek alle xml-links op indexpagina; kies beste kandidaat
        cands = find_xml_candidates(link)
        xml = ""
        for url,_score in cands:
            xml = fetch(url)
            if xml.strip():
                # sanity: moet <ownershipDocument> of <issuerTradingSymbol> bevatten
                if re.search(r"<(?:\w+:)?ownershipDocument\b", xml, re.I) or re.search(r"<(?:\w+:)?issuerTradingSymbol\b", xml, re.I):
                    break
        if not xml.strip():
            # geen bruikbare xml; val terug op een 'filed'-headline
            ev = {"ticker":"", "issuer":(title.split(" - ")[0].strip()), "owner":"Insider", "title":"", "txs":[]}
            evts.append({
            "acc": key,**ev, "when":updated})
            lines.append(build_line(ev, updated, title))
            new_keys.append(key)
            continue

        det = parse_form4(xml)
        evts.append({
            "source":"SEC",
            "xml_url": cands[0][0] if cands else link,
            "ticker": (det.get("ticker") or det.get("issuer") or (title.split(" - ")[0].strip())).upper(),
            "issuer": det.get("issuer") or "",
            "who": det.get("owner") or "Insider",
            "owner": det.get("owner") or "Insider",
            "title": det.get("title") or "",
            "txs": det.get("txs") or [],
            "when": updated
        })
        lines.append(build_line(det, updated, title))
        new_keys.append(key)

        if diag_left>0:
            txs = det.get("txs") or []
            codes = [ (t.get("code","") or "").upper() for t in txs ]
            diag = {c: codes.count(c) for c in ("P","S","M","F")}
            print(f"[diag] {key}  →  P={diag.get('P',0)} S={diag.get('S',0)} M={diag.get('M',0)} F={diag.get('F',0)}")
            diag_left -= 1

    # schrijf outputs
    OUT_TXT.write_text("\n".join(lines)+("\n" if lines else ""), encoding="utf-8")
    with OUT_EVT.open("w", encoding="utf-8") as f:
        for ev in evts:
            f.write(json.dumps(ev)+"\n")
    append_seen(new_keys)

    print(f"[sec] wrote: {OUT_TXT} ({len(lines)} lines); events: {len(evts)} -> {OUT_EVT}; new_seen: {len(new_keys)}")
    return 0

if __name__=="__main__":
    sys.exit(main())
