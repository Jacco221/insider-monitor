from pathlib import Path
import datetime

REP = Path("data/reports"); REP.mkdir(parents=True, exist_ok=True)
SEC = REP/"sec_headlines.txt"
LAT = REP/"latest.txt"

ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
lines = [l.strip() for l in SEC.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()] if SEC.exists() else []

if lines:
    body = "[SEC] Insider Monitor – top nieuws ("+ts+"):\\n• " + "\\n• ".join(lines[:15]) + "\\n"
else:
    body = "[SEC] Insider Monitor – top nieuws ("+ts+"):\\n• [SEC] Geen nieuwe Form 4-headlines in de laatste run.\\n"

LAT.write_text(body, encoding="utf-8")
print("[compose] latest.txt written; items:", len(lines))
