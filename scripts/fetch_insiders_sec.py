import os, sys, re, json, html, pathlib, datetime
from urllib.request import Request, urlopen
from urllib.parse import urljoin

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

def find_xml_url(filing_html_url: str) -> str | None:
    try:
        page = fetch(filing_html_url, timeout=15)
    except Exception:
        return None
    # Zoek een link naar een XML-form4 document (primary doc)
    m = re.search(r'href="([^"]+\.(?:xml|XML))"', page)
    if not m:
        return None
    href = html.unescape(m.group(1))
    if href.startswith("http"):
        return href
    # meestal is het pad relatief t.o.v. de filing page
    base = "https://www.sec.gov"
    return urljoin(base, href)

def _txt(xml: str, path_regex: str) -> str:
    m = re.search(path_regex, xml, flags=re.S|re.I)
    return html.unescape(m.group(1).strip()) if m else ""

def parse_form4_xml(xml: str):
    # Pak globale velden
    ticker = _txt(xml, r"<issuerTradingSymbol>(.*?)</issuerTradingSymbol>")
    issuer = _txt(xml, r"<issuerName>(.*?)</issuerName>") or _txt(xml, r"<issuerName>(.*?)</issuerName>")
    owner  = _txt(xml, r"<rptOwnerName>(.*?)</rptOwnerName>")
    # Non-derivative transacties
    tx_blocks = re.findall(r"<nonDerivativeTransaction>(.+?)</nonDerivativeTransaction>", xml, flags=re.S|re.I)
    txs = []
    for blk in tx_blocks:
        code = _txt(blk, r"<transactionCode>(.*?)</transactionCode>").upper()
        ad   = _txt(blk, r"<transactionAcquiredDisposedCode>\s*<value>(.*?)</value>\s*</transactionAcquiredDisposedCode>").upper()
        if not ad:  # soms als attribuut <transactionAcquiredDisposedCode value="A"/>
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

def build_headline(detail: dict, updated_iso: str):
    tkr   = detail.get("ticker") or ""
    owner = detail.get("owner") or "Insider"
    issuer= detail.get("issuer") or ""
    txs   = detail.get("txs") or []

    # Aggregate netto A vs D, pak voor headline de grootste tx
    total_A = sum(t["shares"]*t["price"] for t in txs if t.get("ad")=="A")
    total_D = sum(t["shares"]*t["price"] for t in txs if t.get("ad")=="D")
    biggest = max(txs, key=lambda t: t.get("shares",0)*t.get("price",0), default=None)

    hot = False
    # HOT als netto acquired positief of als er code 'P' (open-market Purchase) voorkomt
    if total_A > 0 or any(t.get("code","").upper()=="P" for t in txs):
        hot = True

    if biggest:
        sh = biggest["shares"]; pr = biggest["price"]; ad = biggest.get("ad","")
        code = biggest.get("code","")
        notional = sh*pr if (sh and pr) else 0.0
        side = "bought" if ad=="A" or code=="P" else ("sold" if ad=="D" else "transacted")
        tag_hot = "🔥HOT🔥 " if hot else ""
        who = f"{owner}" if owner else "Insider"
        left = (tkr or issuer or "Unknown").upper()
        when = updated_iso.replace("T"," ").replace("Z"," UTC") if updated_iso else "time: n/a"
        if sh and pr:
            return f"- [SEC] {tag_hot}{left} – {who} {side} {int(sh):,} @ ${pr:.2f} (~{human_amount(notional)}) [code={code}/{ad}] ({when})".replace(",", " ")
        else:
            return f"- [SEC] {tag_hot}{left} – {who} {side} [code={code}/{ad}] ({when})"
    else:
        # fallback: geen txs gevonden in XML
        base = (tkr or issuer or "Unknown").upper()
        when = updated_iso.replace("T"," ").replace("Z"," UTC") if updated_iso else "time: n/a"
        return f"- [SEC] {base} – Form 4 filed ({when})"

def main():
    try:
        atom = fetch(ATOM_URL)
    except Exception as e:
        print("[sec] fetch error:", repr(e)); return 0

    entries = parse_atom(atom)
    # Alleen Form 4
    f4 = [e for e in entries if e.get("form","").upper()=="4" or re.search(r"\bForm\s*4\b", e["title"], re.I)]
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    raw_path = RAW_DIR / f"sec_form4_detailed_{ts}.jsonl"
    with raw_path.open("w", encoding="utf-8") as f:
        for e in f4: f.write(json.dumps(e)+"\n")
    print("[sec] saved raw:", raw_path, f"({len(f4)})")

    lines = []
    for e in f4[:50]:
        filing_url = e["link"]
        xml_url = find_xml_url(filing_url) if filing_url else None
        if not xml_url:
            # fallback naar atom-titel
            subj = re.sub(r"^\s*(?:Form\s*4|4)\s*[-:]\s*", "", e["title"], flags=re.I)
            lines.append(f"- [SEC] {subj} – Form 4 filed ({e.get('updated','')})")
            continue
        try:
            xml = fetch(xml_url, timeout=15)
        except Exception as ex:
            subj = re.sub(r"^\s*(?:Form\s*4|4)\s*[-:]\s*", "", e["title"], flags=re.I)
            lines.append(f"- [SEC] {subj} – Form 4 filed ({e.get('updated','')})")
            continue
        detail = parse_form4_xml(xml)
        line = build_headline(detail, e.get("updated",""))
        lines.append(line)

    out = REPORTS / "sec_headlines.txt"
    out.write_text("\n".join(lines)+"\n", encoding="utf-8")
    print("[sec] wrote:", out, f"({len(lines)} lines)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
