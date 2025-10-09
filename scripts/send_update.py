import pathlib, re, subprocess, sys, os

REP = pathlib.Path("data/reports"); REP.mkdir(parents=True, exist_ok=True)
SEC = REP/"sec_headlines.txt"; LATEST = REP/"latest.txt"

def looks_minimal(txt:str)->bool:
    low = txt.strip().lower()
    if not low: return True
    has_header = "== laatste nieuws ==" in low
    bullets = len(re.findall(r"(?m)^\s*[-•]\s+\S+", txt))
    return has_header and bullets == 0

def compose_latest_from_sec():
    lines=[]
    if SEC.exists():
        raw=[l for l in SEC.read_text(encoding="utf-8",errors="ignore").splitlines() if l.strip()]
        lines = raw[:5]  # simpel: top 5 SEC regels
    body = ["== Laatste nieuws =="] + lines
    LATEST.write_text("\n".join(body)+"\n", encoding="utf-8")

def ensure_rss_if_needed():
    txt = LATEST.read_text(encoding="utf-8",errors="ignore") if LATEST.exists() else ""
    if looks_minimal(txt):
        subprocess.run([sys.executable,"scripts/fetch_headlines_rss.py"], check=False)

def main():
    # 1) bouw latest met SEC
    compose_latest_from_sec()
    # 2) vul aan met RSS als het nog header-only is
    ensure_rss_if_needed()
    # 3) verstuur (notify_whatsapp.py heeft zelf ook nog een fallback)
    subprocess.run([sys.executable,"scripts/notify_whatsapp.py"], check=False)

if __name__ == "__main__":
    raise SystemExit(main())
