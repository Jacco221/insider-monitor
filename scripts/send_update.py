import pathlib, re, subprocess, sys

REP = pathlib.Path("data/reports"); REP.mkdir(parents=True, exist_ok=True)
SEC = REP/"sec_headlines.txt"; LATEST = REP/"latest.txt"

def looks_minimal(txt)->bool:
    low=txt.strip().lower()
    if not low: return True
    return "== laatste nieuws ==" in low and not re.search(r"(?m)^\s*[-•]\s+\S+", txt)

def compose_latest_from_sec():
    lines=[]
    if SEC.exists():
        lines=[l for l in SEC.read_text(encoding="utf-8",errors="ignore").splitlines() if l.strip()][:10]
    body=["== Laatste nieuws =="]+lines
    LATEST.write_text("\n".join(body)+"\n", encoding="utf-8")

def main():
    compose_latest_from_sec()
    # geen RSS fallback — puur SEC
    # voorkom lege body: als toch leeg, zet 1 zin
    if looks_minimal(LATEST.read_text(encoding="utf-8",errors="ignore")):
        LATEST.write_text("== Laatste nieuws ==\n- [SEC] Geen nieuwe Form 4-headlines in de laatste run.\n", encoding="utf-8")
    subprocess.run([sys.executable, "scripts/notify_whatsapp.py"], check=False)

if __name__=="__main__":
    sys.exit(main())
