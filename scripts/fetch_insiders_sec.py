import os, sys, re, json, html, pathlib, datetime
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse

BASE = pathlib.Path("data")
RAW_DIR = BASE / "insiders_raw"; RAW_DIR.mkdir(parents=True, exist_ok=True)
REPORTS = pathlib.Path("data/reports"); REPORTS.mkdir(parents=True, exist_ok=True)

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
    """
    Retourneert (xml_url, company_name_from_page, ticker_from_page)
    """
    try:
        page = fetch(filing_html_url, timeout=15)
    except Exception:
        return (None, None, None)

    # 1) XML-link zoeken (primary Form 4 XML)
    m = re.search(r'href="([^"]+\.(?:xml|XML))"', page)
    xml_url = None
    if m:
        href = html.unescape(m.group(1))
        if href.startswith("http"):
            xml_url = href
        else:
            xml_url = urljoin("https://www.sec.gov", href)

    # 2) Company name / ticker uit de filing page proberen te halen
    co = None
    tkr = None

    # veel SEC-pagina's hebben <span class="companyName">NAME (CIK 000..)</span>
    mco = re.search(r'<span class="companyName">\s*([^<]+?)\s*\(CIK', page, flags=re.I)
    if mco:
        co = html.unescape(mco.group(1)).strip()

    # soms: Company Name: <strong>NAME</strong>
    if not co:
        mco2 = re.search(r'Company Name:\s*</[^>]+>\s*([^<]+)<', page, flags=re.I)
        if mco2:
            co = html.unescape(mco2.group(1)).strip()

    # ticker kan op sommige pagina's als: "Trading Symbol: TKR"
    mtkr = re.search(r'Trading Symbol:\s*</[^>]+>\s*([A-Z.\-]{1,10})<', page, flags=re.I)
    if mtkr:
        tkr = mtkr.group(1).strip().upper()

    # fallback: URL-pad bevat soms /data/<CIK>/<ACCESSION>/..., geen naam — dus geen extra info
    return (xml_url, co, tkr)

def _txt(xml: str, path_regex: str) -> str:
    m = re.search(path_regex, xml, flags=re.S|re.I)
    return html.unescape(m.group(1).strip()) if m else ""

def parse_form4_xml(xml: str):
    # XML kan ontbreken of deels leeg zijn; probeer alles wat kan
    ticker = _txt(xml, r"<issuerTradingSymbol>(.*?)</issuerTradingSymbol>")
    issuer = _txt(xml, r"<issuerName>(.*?)</issuerName>")
    owner  = _txt(xml, r"<rptOwnerName>(.*?)</rptOwnerName>")

    # Non-derivative transacties
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
    return {"ticker": ticker, "issuer": issuer, "owner": owner, "txs": txs}

def human_amount(usd: float) -> str:
    if usd >= 1_000_000: return f"${usd/1_000_000:.1f}M"
    if usd >= 1_000:     return f"${usd/1_000:.0f}k"
    return f"${usd:.0f}"

def normalize_from_title(title: str) -> str | None:
    # probeer "Form 4 - COMPANY" of "4 - COMPANY"
    m = re.search(r"Form\s*4\s*[-:]\s*(.+)", title, flags=re.I)
    if m: return m.group(1).split("(")[0].strip()
    m = re.search(r"^\s*4\s*[-:]\s*(.+)", title)
    if m: return m.group(1).split("(")[0].strip()
    return None

def build_headline(detail: dict, updated_iso: str, fallback_name: str | None, fallback_tkr: str | None):
    tkr   = (detail.get("ticker") or fallback_tkr or "").upper()
    issuer= detail.get("issuer") or fallback_name or ""
    owner = detail.get("owner") or "Insider"
    txs   = detail.get("txs") or []

    # als zowel tkr als issuer ontbreken → niets te traden → None
    if not (tkr or issuer):
        return None

    # Aggregate
    total_A = sum(t["shares"]*t["price"] for t in txs if t.get("ad")=="A")
    total_D = sum(t["shares"]*t["price"] for t in txs if t.get("ad")=="D")
    biggest = max(txs, key=lambda t: t.get("shares",0)*t.get("price",0), default=None)
    hot = bool(total_A > 0 or any(t.get("code","").upper()=="P" for t in txs))

    base = (tkr or issuer or "UNKNOWN").upper()
    when = updated_iso.replace("T"," ").replace("Z"," UTC") if updated_iso else "time: n/a"
    tag_hot = "🔥HOT🔥 " if hot else ""
    who = owner

    if biggest:
        sh = biggest["shares"]; pr = biggest["price"]; ad = biggest.get("ad",""); code = biggest.get("code","")
        notional = sh*pr if (sh and pr) else 0.0
        if sh and pr:
            return f"- [SEC] {tag_hot}{base} – {who} {'bought' if (ad=='A' or code=='P') else 'sold' if ad=='D' else 'transacted'} {int(sh):,} @ ${pr:.2f} (~{human_amount(notional)}) [code={code}/{ad}] ({when})".replace(",", " ")
        else:
            return f"- [SEC] {tag_hot}{base} – {who} [code={code}/{ad}] ({when})"
    else:
        # Geen tx details → toch iets tonen (maar alleen als we een base naam/ticker hebben)
        return f"- [SEC] {tag_hot}{base} – Form 4 filed ({when})"

def main():
    # 1) ATOM ophalen + filter op Form 4
    atom = fetch(ATOM_URL)
    entries = parse_atom(atom)
    f4 = [e for e in entries if e.get("form","").upper()=="4" or re.search(r"\bForm\s*4\b", e["title"], re.I)]

    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    raw_path = RAW_DIR / f"sec_form4_enriched_{ts}.jsonl"
    with raw_path.open("w", encoding="utf-8") as f:
        for e in f4: f.write(json.dumps(e)+"\n")
    print("[sec] saved raw:", raw_path, f"({len(f4)})")

    lines = []
    skipped_unknown = 0

    for e in f4[:60]:
        filing_url = e.get("link","")
        xml_url, page_company, page_ticker = find_xml_and_company(filing_url) if filing_url else (None, None, None)

        xml = ""
        if xml_url:
            try:
                xml = fetch(xml_url, timeout=15)
            except Exception:
                xml = ""

        detail = parse_form4_xml(xml) if xml else {"ticker":"", "issuer":"", "owner":"", "txs":[]}

        # Fallbacks voor naam/ticker:
        fallback_name = page_company or normalize_from_title(e.get("title",""))
        fallback_tkr  = page_ticker

        line = build_headline(detail, e.get("updated",""), fallback_name, fallback_tkr)
        if line:
            lines.append(line)
        else:
            skipped_unknown += 1

    # Filter: als er toch nog entries zonder betekenisvolle info waren, die tonen we niet
    out = REPORTS / "sec_headlines.txt"
    out.write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(f"[sec] wrote: {out} ({len(lines)} lines); skipped unknown: {skipped_unknown}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
