import os, re, sys, pathlib, time
from datetime import datetime, timezone
from urllib.request import urlopen, Request

BASE = pathlib.Path("data/reports")
BASE.mkdir(parents=True, exist_ok=True)
OUT_RSS = BASE / "latest_from_rss.txt"
LATEST  = BASE / "latest.txt"

FEEDS = [
    # algemene markt / business
    "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
    # gericht op insider trading / executives
    "https://news.google.com/rss/search?q=insider+trading+OR+executive+buys+OR+SEC+Form+4&hl=en-US&gl=US&ceid=US:en",
]

UA = "Mozilla/5.0 (InsiderMonitor CI; +rss)"

def fetch_rss(url, timeout=10):
    try:
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""

def parse_titles(xml: str):
    # zeer simpele parser: pak <title>…</title> zonder de allereerste feed-titel
    titles = re.findall(r"<title>(.*?)</title>", xml, flags=re.I|re.S)
    if titles:
        titles = titles[1:]  # skip feed title
    cleaned = []
    for t in titles:
        t = re.sub(r"<.*?>", "", t)              # strip html
        t = re.sub(r"\s+", " ", t).strip()
        if t and t.lower() not in {"", "undefined"}:
            cleaned.append(t)
    return cleaned

def build_latest_from_rss():
    seen = set()
    picks = []
    for u in FEEDS:
        xml = fetch_rss(u)
        for t in parse_titles(xml)[:10]:
            k = t.lower()
            if k in seen: 
                continue
            seen.add(k)
            picks.append(t)
            if len(picks) >= 2:
                break
        if len(picks) >= 2:
            break

    if not picks:
        return None

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = [
        "== Insider Monitor – Samenvatting ==",
        f"Generated from public RSS @ {ts}",
        "",
        "== Laatste nieuws ==",
        f"- {picks[0]}",
    ]
    if len(picks) > 1:
        body.append(f"- {picks[1]}")
    OUT_RSS.write_text("\n".join(body) + "\n", encoding="utf-8")
    return OUT_RSS

def looks_like_seed_or_minimal(text: str) -> bool:
    t = text.lower()
    return ("seed gebruikt" in t) or ("geen headlines" in t) or ("no source report" in t)

def main():
    # Alleen overschrijven als latest.txt er niet is, leeg is, of een seed/minimaal bericht bevat
    use_rss = False
    if not LATEST.exists() or LATEST.stat().st_size == 0:
        use_rss = True
    else:
        txt = LATEST.read_text(encoding="utf-8", errors="ignore")
        if looks_like_seed_or_minimal(txt):
            use_rss = True

    if use_rss:
        src = build_latest_from_rss()
        if src:
            # Vervang latest.txt door de RSS-versie
            LATEST.write_text(src.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            print("[rss] latest.txt updated from RSS:", src)
        else:
            print("[rss] no headlines found from RSS feeds")
    else:
        print("[rss] latest.txt already has real headlines; no need to replace")

if __name__ == "__main__":
    main()
