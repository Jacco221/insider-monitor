import os, sys, re, json, html, pathlib, datetime
from urllib.request import Request, urlopen
from urllib.parse import urljoin

BASE = pathlib.Path("data")
RAW_DIR = BASE / "insiders_raw"; RAW_DIR.mkdir(parents=True, exist_ok=True)
REPORTS = pathlib.Path("data/reports"); REPORTS.mkdir(parents=True, exist_ok=True)
STATE_DIR = BASE / "state"; STATE_DIR.mkdir(parents=True, exist_ok=True)
SEEN_PATH = STATE_DIR / "sec_seen.jsonl"

ATOM_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&count=200&output=atom"
UA = os.getenv("SEC_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")

def fetch(url: str, timeout=15) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")

def parse_atom(atom_xml: str):
    entries = []
    for block in re.findall(r"<entry>(.+?)</entry>", atom_xml, flags=re.S|re.I):
        def tag(name, attr=None):
            if attr:
                m = re.search(fr"<{name}[^>]*{attr}=\"([^\"]+)\"[^>]*/?>", block, flags=re.I)
                return m.group(1) if m else ""
            m = re.search(fr"<{name}[^>]*>(.*?)</{name}>", block, flags=re.S|re.I)
            return m.group(1).strip() if m else ""
        title   = html.unescape(re.sub(r"\s+", " ", re.sub(r"<.*?>","", tag("title")))).strip()
        link    = tag("link","href") or ""
        updated = tag("updated") or ""
        cats    = re.findall(r'<category[^>]*term="([^"]+)"', block, flags=re.I)
        form    = (cats[0].strip().upper() if cats else "")
        if not form:
            m2 = re.search(r"\bForm\s*([A-Za-z0-9]+)\b", title, flags=re.I)
            form = m2.group(1).upper() if m2 else ""
        entries.append({"title": title, "link": link, "updated": updated, "form": form})
    return entries

def find_xml_and_company(filing_html_url: str):
    try:
        page = fetch(filing_html_url, timeout=15)
    except Exception:
        return (None, None, None, None)
    m = re.search(r'href="([^"]+\.(?:xml|XML))"', page)
    xml_url = None
    if m:
        href = html.unescape(m.group(1))
        xml_url = href if href.startswith("http") else urljoin("https://www.sec.gov", href)
    co = None; tkr = None
    mco = re.search(r'<span class="companyName">\s*([^<]+?)\s*\(CIK', page, flags=re.I)
    if mco: co = html.unescape(mco.group(1)).strip()
    if not co:
        mco2 = re.search(r'Company Name:\s*</[^>]+>\s*([^<]+)<', page, flags=re.I)
        if mco2: co = html.unescape(mco2.group(1)).strip()
    mtkr = re.search(r'Trading Symbol:\s*</[^>]+>\s*([A-Z.\-]{1,10})<', page, flags=re.I)
    if mtkr: tkr = mtkr.group(1).strip().upper()
    macc = re.search(r'Accession Number:\s*</[^>]+>\s*([0-9-]+)<', page, flags=re.I)
    acc = macc.group(1).strip() if macc else None
    return (xml_url, co, tkr, acc)

def _txt(xml: str, path_regex: str) -> str:
    m = re.search(path_regex, xml, flags=re.S|re.I)
    return html.unescape(m.group(1).strip()) if m else ""

def parse_form4_xml(xml: str):
    ticker = _txt(xml, r"<issuerTradingSymbol>(.*?)</issuerTradingSymbol>")
    issuer = _txt(xml, r"<issuerName>(.*?)</issuerName>")
    owner  = _txt(xml, r"<rptOwnerName>(.*?)</rptOwnerName>")
    is_officer = bool(re.search(r"<isOfficer>\s*1\s*</isOfficer>", xml))
    is_director= bool(re.search(r"<isDirector>\s*1\s*</isDirector>", xml))
    officer_title = _txt(xml, r"<officerTitle>(.*?)</officerTitle>")

    tx_blocks = re.findall(r"<nonDerivativeTransaction>(.+?)</nonDerivativeTransaction>", xml, flags=re.S|re.I)
    txs = []
    for blk in tx_blocks:
        code = _txt(blk, r"<transactionCode>(.*?)</transactionCode>").upper()
        ad   = _txt(blk, r"<transactionAcquiredDisposedCode>\s*<value>(.*?)</value>\s*</transactionAcquiredDisposedCode>").upper()
        if not ad:
            m = re.search(r"<transactionAcquiredDisposedCode[^>]*value=\"([AD])\"[^>]*/?>", blk, flags=re.I)
            ad = m.group(1).upper() if m else ""
        shares = _txt(blk, r"<transactionShares>\s*<value>(.*?)</value>\s*</transactionShares>")
        price  = _txt(blk, r"<transactionPricePerShare>\s*<value>(.*?)</value>\s*</transactionPricePerShare>")
        date   = _txt(blk, r"<transactionDate>\s*<value>(.*?)</value>\s*</transactionDate>")
        try: sh = float(shares.replace(",",""))
        except: sh = 0.0
        try: pr = float(price.replace(",",""))
        except: pr = 0.0
        txs.append({"code":code, "ad":ad, "shares":sh, "price":pr, "date":date})
    return {
        "ticker": ticker, "issuer": issuer, "owner": owner,
        "txs": txs, "is_officer": is_officer, "is_director": is_director, "officer_title": officer_title
    }

def human_amount(usd: float) -> str:
    if usd >= 1_000_000: return f"${usd/1_000_000:.1f}M"
    if usd >= 1_000:     return f"${usd/1_000:.0f}k"
    return f"${usd:.0f}"

def normalize_from_title(title: str) -> str | None:
    m = re.search(r"Form\s*4\s*[-:]\s*(.+)", title, flags=re.I)
    if m: return m.group(1).split("(")[0].strip()
    m = re.search(r"^\s*4\s*[-:]\s*(.+)", title)
    if m: return m.group(1).split("(")[0].strip()
    return None

def load_seen():
    seen = set()
    if SEEN_PATH.exists():
        with SEEN_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    j = json.loads(line); k = j.get("key")
                    if k: seen.add(k)
                except: pass
    return seen

def append_seen(keys):
    if not keys: return
    with SEEN_PATH.open("a", encoding="utf-8") as f:
        for k in keys:
            f.write(json.dumps({"key": k, "ts": datetime.datetime.utcnow().isoformat()+"Z"})+"\n")

# -------- Heuristieken (rol-bewust) --------
def summarize_sides(txs):
    tot_A = sum(t["shares"]*t["price"] for t in txs if t.get("ad")=="A" or t.get("code")=="P")
    tot_S = sum(t["shares"]*t["price"] for t in txs if t.get("code")=="S" or t.get("ad")=="D")
    tot_M = sum(t["shares"]*t["price"] for t in txs if t.get("code")=="M")
    tot_F = sum(t["shares"]*t["price"] for t in txs if t.get("code")=="F")
    biggest = max(txs, key=lambda t: t.get("shares",0)*t.get("price",0), default=None)
    return tot_A, tot_S, tot_M, tot_F, biggest

def role_bucket(detail):
    title = (detail.get("officer_title") or "").lower()
    if detail.get("is_officer") or detail.get("is_director"):
        # CEO/CFO/President/Chair → top
        if any(k in title for k in ["chief executive", "ceo", "chief financial", "cfo", "president", "chair", "chairman"]):
            return "top"
        return "officer_director"
    return "other"

def should_include(detail):
    txs = detail.get("txs",[])
    if not txs: return (False, None, None)
    bucket = role_bucket(detail)
    tot_A, tot_S, tot_M, tot_F, biggest = summarize_sides(txs)

    # Drempels per rol
    if bucket in ("top", "officer_director"):
        buy_thresh  = 50_000
        sell_thresh = 250_000
    else:
        buy_thresh  = 250_000
        sell_thresh = 500_000

    # BUY
    if tot_A >= buy_thresh:
        return (True, "BUY", biggest)

    # SELL (filter administratief M/F)
    net_sell_like = tot_S
    if net_sell_like >= sell_thresh and net_sell_like > (tot_M + tot_F) * 1.2:
        return (True, "SELL", biggest)

    return (False, None, None)

def is_hot(side, detail, biggest):
    txs = detail.get("txs",[])
    bucket = role_bucket(detail)
    tot_A, tot_S, tot_M, tot_F, _ = summarize_sides(txs)

    if side == "BUY":
        if bucket == "top":
            return (tot_A >= 100_000) or any(t.get("code")=="P" for t in txs)
        if bucket == "officer_director":
            return (tot_A >= 150_000) or any(t.get("code")=="P" for t in txs)
        return (tot_A >= 1_000_000) or any(t.get("code")=="P" for t in txs)

    if side == "SELL":
        if bucket == "top":
            return (tot_S >= 750_000) and (tot_S > (tot_M + tot_F) * 1.5)
        if bucket == "officer_director":
            return (tot_S >= 1_000_000) and (tot_S > (tot_M + tot_F) * 1.5)
        return (tot_S >= 2_000_000) and (tot_S > (tot_M + tot_F) * 2.0)

    return False

def build_headline(detail, updated_iso, fallback_name, fallback_tkr):
    tkr   = (detail.get("ticker") or fallback_tkr or "").upper()
    issuer= detail.get("issuer") or fallback_name or ""
    owner = detail.get("owner") or "Insider"
    ok, side, biggest = should_include(detail)
    if not ok: return None
    base = (tkr or issuer or "").upper()
    if not base: return None

    when = updated_iso.replace("T"," ").replace("Z"," UTC") if updated_iso else "time: n/a"
    tag = "🔥HOT🔥 " if is_hot(side, detail, biggest) else ""
    sh = biggest.get("shares",0.0); pr = biggest.get("price",0.0); code = biggest.get("code",""); ad = biggest.get("ad","")
    notional = sh*pr if (sh and pr) else 0.0
    if sh and pr:
        return f"- [SEC] {tag}{base} – {owner} {side} {int(sh):,} @ ${pr:.2f} (~{human_amount(notional)}) [code={code}/{ad}] ({when})".replace(",", " ")
    else:
        return f"- [SEC] {tag}{base} – {owner} {side} [code={code}/{ad}] ({when})"

def normalize_from_title(title: str) -> str | None:
    m = re.search(r"Form\s*4\s*[-:]\s*(.+)", title, flags=re.I)
    if m: return m.group(1).split("(")[0].strip()
    m = re.search(r"^\s*4\s*[-:]\s*(.+)", title)
    if m: return m.group(1).split("(")[0].strip()
    return None

def load_title_owner(title: str):
    m_owner = re.search(r"4\s*[-:]\s*([A-Za-z0-9 .,'&-]+)\s*\((?:Reporting|Filer)\)", title, flags=re.I)
    return m_owner.group(1).strip() if m_owner else None

def main():
    atom = fetch(ATOM_URL)
    entries = parse_atom(atom)
    f4 = [e for e in entries if e.get("form","").upper()=="4" or re.search(r"\bForm\s*4\b", e["title"], re.I)]
    print("[sec] fetched Form 4 entries:", len(f4))

    seen = load_seen()
    new_keys = []; lines = []
    dedup_skips = non_tradable_skips = 0

    for e in f4[:120]:
        link = e.get("link",""); updated = e.get("updated","")
        xml_url, page_company, page_ticker, accession = find_xml_and_company(link) if link else (None, None, None, None)
        key = accession or (link + "|" + updated)
        if key in seen:
            dedup_skips += 1
            continue

        xml = ""
        if xml_url:
            try: xml = fetch(xml_url, timeout=15)
            except Exception: xml = ""

        detail = parse_form4_xml(xml) if xml else {"ticker":"", "issuer":"", "owner":"", "txs":[], "is_officer":False, "is_director":False, "officer_title":""}
        fallback_name = page_company or normalize_from_title(e.get("title",""))
        fallback_tkr  = page_ticker

        # Indien owner in titel staat, gebruik dat als hint
        owner_hint = load_title_owner(e.get("title",""))
        if owner_hint and not detail.get("owner"):
            detail["owner"] = owner_hint

        line = build_headline(detail, updated, fallback_name, fallback_tkr)
        if line:
            lines.append(line); new_keys.append(key)
        else:
            non_tradable_skips += 1

    out = REPORTS / "sec_headlines.txt"
    out.write_text("\n".join(lines)+"\n", encoding="utf-8")
    append_seen(new_keys)
    print(f"[sec] wrote: {out} ({len(lines)} lines); new_seen: {len(new_keys)}; dedup_skips: {dedup_skips}; filtered: {non_tradable_skips}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
