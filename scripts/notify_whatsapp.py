import os, re, sys, pathlib
from datetime import datetime

BASE = pathlib.Path("data/reports")

def _norm(s:str) -> str:
    return s.replace("\r\n","\n").replace("\r","\n")

def _parse_top_news(txt: str) -> list[str]:
    txt = _norm(txt)
    lines = [ln.rstrip() for ln in txt.split("\n")]

    # sectie "Laatste nieuws"
    section = []
    start = None
    for i, ln in enumerate(lines):
        if "Laatste nieuws" in ln:
            start = i+1; break
    if start:
        for j in range(start, len(lines)):
            if lines[j].strip().startswith("=="): break
            section.append(lines[j])

    bullets = [re.sub(r"^\s*[•\-]\s*", "", ln).strip() for ln in section
               if ln.strip().startswith(("-", "•"))]

    if not bullets:
        bullets = [ln.strip() for ln in section if ln.strip()]

    scored = []
    for ln in bullets:
        score = 0.0
        mm = re.search(r"(\d\.\d+)", ln)
        if mm: score = float(mm.group(1))
        if "HOT" in ln.upper(): score += 0.2
        scored.append((-(score), ln))
    scored.sort()
    return [ln for _, ln in scored[:2]]

def _detect_source(text:str, path:pathlib.Path) -> str:
    low = text.lower()
    if "generated from public rss" in low: return "[RSS]"
    if "seed gebruikt" in low or "seed" in path.name: return "[SEED]"
    if "insider monitor – samenvatting" in low: return "[PIPELINE]"
    return "[SEC]"

def _pick_body_and_source():
    latest = BASE/"latest.txt"
    if not latest.exists(): 
        return "Insider Monitor: geen rapporten gevonden.", "[SEED]"
    txt = latest.read_text(encoding="utf-8", errors="ignore")
    bullets = _parse_top_news(txt)
    src = _detect_source(txt, latest)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if bullets:
        # markeer HOT extra
        out = []
        for b in bullets:
            if "HOT" in b.upper():
                out.append(f"🔥HOT🔥 {b}")
            else:
                out.append(b)
        body = f"{src} Insider Monitor – top nieuws ({ts}):\n• " + "\n• ".join(out)
    else:
        snippet = re.sub(r"\s+"," ",txt).strip()[:200]
        body = f"{src} Insider Monitor – samenvatting ({ts}): {snippet}"
    return body

def sanitize_to(raw:str)->str:
    cleaned = "".join(ch for ch in raw if ch.isdigit() or ch=="+").strip()
    if cleaned.count("+")>1: cleaned = "+"+cleaned.replace("+","")
    return cleaned

def main()->int:
    body = _pick_body_and_source()
    sid = os.getenv("TWILIO_ACCOUNT_SID","").strip()
    tok = os.getenv("TWILIO_AUTH_TOKEN","").strip()
    to  = sanitize_to(os.getenv("ALERT_TO_INSIDER",""))
    if not re.fullmatch(r"\+\d{10,15}", to):
        print("To ongeldig; body:\n",body); return 0
    try:
        from twilio.rest import Client
        msg = Client(sid,tok).messages.create(
            body=body, from_="whatsapp:+14155238886", to=f"whatsapp:{to}"
        )
        print("WhatsApp verzonden! SID:", msg.sid); return 0
    except Exception as e:
        print("Fout bij versturen:", e); print("Body zou zijn:\n", body); return 0

if __name__=="__main__":
    raise SystemExit(main())
