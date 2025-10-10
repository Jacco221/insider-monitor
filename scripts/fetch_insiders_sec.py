#!/usr/bin/env python3
import os, re, html, json, pathlib, datetime, time
from urllib.request import Request, urlopen
from urllib.parse import urljoin

BASE = pathlib.Path("data")
REPORTS = BASE / "reports"; REPORTS.mkdir(parents=True, exist_ok=True)
STATE = BASE / "state"; STATE.mkdir(parents=True, exist_ok=True)
SEEN  = STATE / "sec_seen.jsonl"
OUT   = REPORTS / "sec_headlines.txt"

UA = os.getenv("SEC_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")
ATOM_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&count=200&output=atom"

def _fetch(url, timeout=20, retries=2):
    for i in range(retries+1):
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:
            time.sleep(1+i)
    return ""

def _parse_atom(xml:str):
    out=[]
    for e in re.findall(r"<entry>(.+?)</entry>", xml, flags=re.S|re.I):
        def tag(name, attr=None):
            if attr:
                m=re.search(fr"<{name}[^>]*{attr}=\"([^\"]+)\"[^>]*/?>", e, flags=re.I)
                return m.group(1) if m else ""
            m=re.search(fr"<{name}[^>]*>(.*?)</{name}>", e, flags=re.S|re.I)
            return m.group(1).strip() if m else ""
        title = html.unescape(re.sub(r"<.*?>","", tag("title")))
        link  = tag("link","href") or ""
        updated = tag("updated") or ""
        out.append({"title":title,"link":link,"updated":updated})
    return out

def _find_xml_url(index_html:str):
    m=re.search(r'href="([^"]+\.(?:xml|XML))"', index_html)
    if not m: return None
    href = html.unescape(m.group(1))
    return href if href.startswith("http") else urljoin("https://www.sec.gov", href)

# ---- XML helpers (namespace-aware via (?:\w+:)? ) ----
def _grab(xml, tag):
    m=re.search(fr"<(?:\w+:)?{tag}\b[^>]*>(.*?)</(?:\w+:)?{tag}>", xml, flags=re.S|re.I)
    return html.unescape((m.group(1) if m else "").strip())

def _grab_val(xml, tag):
    m=re.search(fr"<(?:\w+:)?{tag}\b[^>]*>\s*(?:<(?:\w+:)?value[^>]*>)?\s*([^<]+)", xml, flags=re.S|re.I)
    return html.unescape((m.group(1) if m else "").strip())

def _iter_blocks(xml, tag):
    return re.findall(fr"<(?:\w+:)?{tag}\b[^>]*>(.+?)</(?:\w+:)?{tag}>", xml, flags=re.S|re.I)

def _parse_form4(xml):
    tkr=_grab(xml,"issuerTradingSymbol")
    iss=_grab(xml,"issuerName")
    own=_grab(xml,"rptOwnerName")
    off=_grab(xml,"officerTitle")
    txs=[]
    for blk in _iter_blocks(xml,"nonDerivativeTransaction")+_iter_blocks(xml,"derivativeTransaction"):
        code=_grab(blk,"transactionCode").upper()
        ad  =_grab_val(blk,"transactionAcquiredDisposedCode").upper()
        sh  =_grab_val(blk,"transactionShares")
        pr  =(_grab_val(blk,"transactionPricePerShare") 
              or _grab_val(blk,"conversionOrExercisePrice")
              or _grab_val(blk,"exercisePrice"))
        tv  =_grab_val(blk,"transactionTotalValue")
        try: shf=float((sh or "0").replace(",",""))
        except: shf=0.0
        pf=0.0
        if pr:
            try: pf=float((pr or "0").replace(",",""))
            except: pf=0.0
        elif tv and shf>0:
            try: pf=float((tv or "0").replace(",",""))/shf
            except: pf=0.0
        txs.append({"code":code,"ad":ad,"shares":shf,"price":pf})
    return {"ticker":tkr,"issuer":iss,"owner":own,"title":off,"txs":txs}

def _human(v):
    if v>=1_000_000: return f"${v/1_000_000:.1f}M"
    if v>=1_000:     return f"${v/1_000:.0f}k"
    return f"${v:.0f}"

def _summarize(txs):
    buy=sum(t["shares"]*t["price"] for t in txs if t.get("ad")=="A" or t.get("code")=="P")
    sell=sum(t["shares"]*t["price"] for t in txs if t.get("ad")=="D" or t.get("code")=="S")
    return buy, sell

def main():
    atom=_fetch(ATOM_URL)
    entries=_parse_atom(atom)
    print("[sec] entries:", len(entries))
    lines=[]

    for e in entries[:180]:
        idx_html = _fetch(e["link"]) if e.get("link") else ""
        xml_url  = _find_xml_url(idx_html) if idx_html else None
        when = (e.get("updated") or "").replace("T"," ").replace("Z"," UTC")

        if not xml_url:
            base=(e.get("title","") or "UNKNOWN").split(" (")[0].strip() or "UNKNOWN"
            lines.append(f"- [SEC] {base} – Form 4 filed ({when})")
            continue

        xml=_fetch(xml_url)
        if not xml.strip():
            base=(e.get("title","") or "UNKNOWN").split(" (")[0].strip() or "UNKNOWN"
            lines.append(f"- [SEC] {base} – Form 4 filed ({when})")
            continue

        d=_parse_form4(xml)
        base=(d.get("ticker") or d.get("issuer") or (e.get("title","").split(' (')[0].strip()) or "UNKNOWN").upper()
        who = d.get("owner") or "Insider"
        buy, sell = _summarize(d.get("txs") or [])
        if buy>0:
            lines.append(f"- [SEC] {base} – {who} BUY ~{_human(buy)} ({when})")
        elif sell>0:
            lines.append(f"- [SEC] {base} – {who} SELL ~{_human(sell)} ({when})")
        else:
            lines.append(f"- [SEC] {base} – {who}: Form 4 filed ({when})")

    OUT.write_text("\n".join(lines)+("\n" if lines else ""), encoding="utf-8")
    print(f"[sec] wrote: {OUT} ({len(lines)} lines)")
    return 0

if __name__=="__main__":
    import sys; sys.exit(main())
