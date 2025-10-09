import os, sys, re, json, html, pathlib, datetime
from urllib.request import Request, urlopen
from urllib.parse import urljoin

BASE = pathlib.Path("data")
REPORTS = BASE / "reports"; REPORTS.mkdir(parents=True, exist_ok=True)
STATE_DIR = BASE / "state"; STATE_DIR.mkdir(parents=True, exist_ok=True)
SEEN_PATH = STATE_DIR / "sec_seen.jsonl"

ATOM_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&count=200&output=atom"
UA = os.getenv("SEC_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")

def fetch(url, timeout=15):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")

def parse_atom(xml):
    out=[]
    for block in re.findall(r"<entry>(.+?)</entry>", xml, flags=re.S|re.I):
        def tag(n,a=None):
            if a:
                m=re.search(fr"<{n}[^>]*{a}=\"([^\"]+)\"", block, flags=re.I); return m.group(1) if m else ""
            m=re.search(fr"<{n}[^>]*>(.*?)</{n}>", block, flags=re.S|re.I); return m.group(1).strip() if m else ""
        title = html.unescape(re.sub(r"<.*?>","", tag("title")))
        link  = tag("link","href") or ""
        updated = tag("updated") or ""
        cats = re.findall(r'<category[^>]*term="([^"]+)"', block, flags=re.I)
        form = (cats[0].strip().upper() if cats else "")
        if not form and re.search(r"\bForm\s*4\b", title, flags=re.I): form="4"
        out.append({"title":title, "link":link, "updated":updated, "form":form})
    return out

def find_xml_and_company(url):
    try:
        page = fetch(url, 15)
    except Exception:
        return None, None, None, None
    m = re.search(r'href="([^"]+\.(?:xml|XML))"', page)
    xml_url = None
    if m:
        href = html.unescape(m.group(1))
        xml_url = href if href.startswith("http") else urljoin("https://www.sec.gov", href)
    mco = re.search(r'<span class="companyName">\s*([^<]+?)\s*\(CIK', page, flags=re.I)
    co = html.unescape(mco.group(1)).strip() if mco else None
    mtkr = re.search(r'Trading Symbol:\s*</[^>]+>\s*([A-Z.\-]{1,10})<', page, flags=re.I)
    tkr = mtkr.group(1).strip().upper() if mtkr else None
    macc = re.search(r'Accession Number:\s*</[^>]+>\s*([0-9-]+)<', page, flags=re.I)
    acc = macc.group(1).strip() if macc else None
    return xml_url, co, tkr, acc

def _txt(xml, rx):
    m=re.search(rx, xml, flags=re.S|re.I)
    return html.unescape(m.group(1).strip()) if m else ""

def parse_form4_xml(xml):
    ticker = _txt(xml, r"<issuerTradingSymbol>(.*?)</issuerTradingSymbol>")
    issuer = _txt(xml, r"<issuerName>(.*?)</issuerName>")
    owner  = _txt(xml, r"<rptOwnerName>(.*?)</rptOwnerName>")
    officer_title = _txt(xml, r"<officerTitle>(.*?)</officerTitle>")
    txs=[]
    for blk in re.findall(r"<nonDerivativeTransaction>(.+?)</nonDerivativeTransaction>", xml, flags=re.S|re.I):
        code = _txt(blk, r"<transactionCode>(.*?)</transactionCode>").upper()
        ad   = _txt(blk, r"<transactionAcquiredDisposedCode>\s*<value>(.*?)</value>").upper()
        sh   = _txt(blk, r"<transactionShares>\s*<value>(.*?)</value>")
        pr   = _txt(blk, r"<transactionPricePerShare>\s*<value>(.*?)</value>")
        dt   = _txt(blk, r"<transactionDate>\s*<value>(.*?)</value>")
        try: shf = float(sh.replace(",",""))
        except: shf = 0.0
        try: prf = float(pr.replace(",",""))
        except: prf = 0.0
        txs.append({"code":code,"ad":ad,"shares":shf,"price":prf,"date":dt})
    return {"ticker":ticker,"issuer":issuer,"owner":owner,"title":officer_title,"txs":txs}

def human(usd: float) -> str:
    return f"${usd/1_000_000:.1f}M" if usd>=1_000_000 else (f"${usd/1_000:.0f}k" if usd>=1_000 else f"${usd:.0f}")

def summarize(detail):
    txs=detail["txs"]
    totP = sum(t["shares"]*t["price"] for t in txs if t.get("code")=="P")
    totS = sum(t["shares"]*t["price"] for t in txs if t.get("code")=="S")
    totM = sum(t["shares"]*t["price"] for t in txs if t.get("code")=="M")
    totF = sum(t["shares"]*t["price"] for t in txs if t.get("code")=="F")
    biggest = max(txs, key=lambda t: t.get("shares",0)*t.get("price",0), default=None)
    return totP, totS, totM, totF, biggest

def build_line(detail, updated, fallback_name, fallback_tkr):
    # Neem elke filing mee waar minstens 1 non-derivative tx in staat
    if not detail["txs"]:
        return None
    tkr=(detail.get("ticker") or fallback_tkr or "").upper()
    name = tkr or (fallback_name or "").upper() or "UNKNOWN"
    owner=detail.get("owner") or "Insider"
    totP,totS,totM,totF,big= summarize(detail)
    when=updated.replace("T"," ").replace("Z"," UTC") if updated else "time:n/a"
    parts=[]
    if totP: parts.append(f"P~{human(totP)}")
    if totS: parts.append(f"S~{human(totS)}")
    if totM: parts.append(f"M~{human(totM)}")
    if totF: parts.append(f"F~{human(totF)}")
    meta = ", ".join(parts) if parts else "tx parsed"
    line = f"- [SEC] {name} – {owner}: {meta} ({when})"
    return {"line":line, "prio": (int((totP+totS+totM+totF)/1000))}

def load_seen():
    s=set()
    if SEEN_PATH.exists():
        for line in SEEN_PATH.read_text(encoding="utf-8").splitlines():
            try:
                j=json.loads(line); k=j.get("key")
                if k: s.add(k)
            except: pass
    return s

def main():
    atom=fetch(ATOM_URL)
    entries=parse_atom(atom)
    f4=[e for e in entries if e.get("form","").upper()=="4"]
    seen=load_seen(); new_keys=[]; items=[]
    for e in f4[:180]:
        link=e.get("link",""); updated=e.get("updated","")
        xml_url, co, tkr, acc = find_xml_and_company(link) if link else (None,None,None,None)
        key = acc or (link + "|" + updated)
        if key in seen: continue
        xml=""
        if xml_url:
            try: xml=fetch(xml_url,15)
            except Exception: xml=""
        detail = parse_form4_xml(xml) if xml else {"ticker":"","issuer":"","owner":"","title":"","txs":[]}
        # fallback owner uit title
        mm=re.search(r"4\s*[-:]\s*([A-Za-z0-9 .,'&-]+)\s*\((?:Reporting|Filer)\)", e.get("title",""), flags=re.I)
        if mm and not detail.get("owner"): detail["owner"]=mm.group(1).strip()
        built=build_line(detail, updated, co, tkr)
        if built: items.append(built); new_keys.append(key)
    items.sort(key=lambda x: x["prio"], reverse=True)
    lines=[it["line"] for it in items]
    out = REPORTS/"sec_headlines.txt"
    out.write_text("\n".join(lines)+"\n", encoding="utf-8")
    if new_keys:
        with SEEN_PATH.open("a", encoding="utf-8") as f:
            for k in new_keys: f.write(json.dumps({"key":k,"ts":datetime.datetime.utcnow().isoformat()+"Z"})+"\n")
    print(f"[sec] wrote: {out} ({len(lines)} lines); new_seen: {len(new_keys)})")
    return 0

if __name__=="__main__":
    sys.exit(main())
