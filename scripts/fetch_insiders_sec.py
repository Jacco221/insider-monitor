import os, sys, re, json, time, pathlib, datetime, html
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BASE = pathlib.Path("data")
RAW_DIR = BASE / "insiders_raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)
REPORTS = pathlib.Path("data/reports")
REPORTS.mkdir(parents=True, exist_ok=True)

ATOM_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&count=100&output=atom"
UA = os.getenv("SEC_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")

# simpele, robuuste Atom-parser
def fetch_atom(url: str, timeout=15) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")

def parse_entries(atom_xml: str):
    # minimalistische parse: <entry> … <title> … <link href="…"> … <updated> … <category term="…"> … </entry>
    entries = []
    for block in re.findall(r"<entry>(.+?)</entry>", atom_xml, flags=re.S|re.I):
        def g(tag, attr=None):
            if attr:
                m = re.search(fr"<{tag}[^>]*{attr}=\"([^\"]+)\"[^>]*/?>", block, flags=re.I)
                return m.group(1) if m else ""
            m = re.search(fr"<{tag}[^>]*>(.*?)</{tag}>", block, flags=re.S|re.I)
            return m.group(1).strip() if m else ""
        title = html.unescape(re.sub(r"\s+", " ", re.sub(r"<.*?>","", g("title")))).strip()
        link  = g("link", "href") or ""
        updated = g("updated") or g("filing-date") or ""
        entries.append({"title": title, "link": link, "updated": updated})
    return entries

def is_hot(title: str) -> bool:
    t = title.lower()
    hot_words = ("purchase","purchased","buy","bought","acquired","acquisition of","acq.")
    return any(w in t for w in hot_words)

def normalize_company_from_title(title: str) -> str:
    # voor titels als "Form 4 - COMPANY NAME (CIK 000000)"
    m = re.search(r"Form\s*4\s*-\s*(.+)$", title, flags=re.I)
    if m: return m.group(1).strip()
    return title

def main():
    try:
        xml = fetch_atom(ATOM_URL)
    except Exception as e:
        print("[sec] fetch error:", repr(e))
        return 0

    entries = parse_entries(xml)
    if not entries:
        print("[sec] no entries parsed"); return 0

    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    raw_path = RAW_DIR / f"sec_form4_{ts}.jsonl"
    with raw_path.open("w", encoding="utf-8") as f:
        for e in entries: f.write(json.dumps(e)+"\n")
    print("[sec] saved raw:", raw_path)

    # Bouw headline regels
    lines = []
    seen = set()
    for e in entries[:40]:  # neem de meest recente ~40
        title = e["title"]
        company = normalize_company_from_title(title)
        link = e["link"]
        up = e["updated"].replace("T"," ").replace("Z"," UTC")
        # Heuristiek: markeer HOT bij duidende woorden; anders neutraal
        hot = "🔥HOT🔥 " if is_hot(title) else ""
        line = f"- [SEC] {hot}{company} – Form 4 filed ({up})"
        # bewaar link (als extra regel, optioneel uitzetbaar)
        # line += f" – {link}"
        key = (company, up)
        if key in seen: continue
        seen.add(key)
        lines.append(line)

    if not lines:
        print("[sec] no headlines built"); return 0

    out = REPORTS / "sec_headlines.txt"
    out.write_text("\n".join(lines)+"\n", encoding="utf-8")
    print("[sec] wrote:", out, f"({len(lines)} lines)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
