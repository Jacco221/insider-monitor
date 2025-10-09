import os, re, sys, pathlib
from datetime import datetime

BASE = pathlib.Path("data/reports")

HOT_WORDS = {"plunge","tumbles","crash","collapse","halts","investigation","probe","fraud",
             "lawsuit","downgrade","withdraws guidance","cuts guidance","misses","short seller",
             "bankruptcy","chapter 11","restatement","data breach","hack","recall","sec charges",
             "whistleblower","criminal","allegation","sanction","beats","surge","spikes","soars",
             "upgrade","raises guidance","acquisition","buyout","takeover","buyback","special dividend",
             "partnership","approval","fda approves","sec filing","form 4","insider buys","insider purchase"}

def _norm(s:str) -> str:
    return s.replace("\r\n","\n").replace("\r","\n")

def _parse_top_news(txt: str):
    txt = _norm(txt)
    lines = [ln.rstrip() for ln in txt.split("\n")]
    # Vind sectie
    section, start = [], None
    for i, ln in enumerate(lines):
        if "Laatste nieuws" in ln:
            start = i+1; break
    if start is not None:
        for j in range(start, len(lines)):
            if lines[j].strip().startswith("=="): break
            section.append(lines[j])
    bullets = [re.sub(r"^\s*[•\-]\s*", "", ln).strip() for ln in section if ln.strip().startswith(("-", "•"))]
    plain   = [ln.strip() for ln in section if ln.strip()]
    return bullets or plain

def _detect_source(text:str) -> str:
    low = text.lower()
    if "generated from public rss" in low: return "[RSS]"
    if "form 4" in low or "[sec]" in low: return "[SEC]"
    if "insider monitor – samenvatting" in low: return "[PIPELINE]"
    return "[PIPELINE]"

def _hot_tag(line:str, score_hint:float=0.0)->str:
    if score_hint >= 0.85: return "🔥HOT🔥 "
    low = line.lower()
    return "🔥HOT🔥 " if any(w in low for w in HOT_WORDS) else ""

def build_body():
    latest = BASE/"latest.txt"
    if not latest.exists() or latest.stat().st_size == 0:
        return "[SEED] Insider Monitor – geen rapport gevonden."
    txt = latest.read_text(encoding="utf-8", errors="ignore")
    src = _detect_source(txt)
    items = _parse_top_news(txt)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    if items:
        # maak maximaal 2 regels, HOT markering eenvoudig
        out = []
        for ln in items[:2]:
            tag = _hot_tag(ln)
            out.append(f"{tag}{ln}")
        return f"{src} Insider Monitor – top nieuws ({ts}):\n• " + "\n• ".join(out)
    # fallback: altijd tekst sturen, nooit leeg
    snippet = re.sub(r"\s+"," ", _norm(txt)).strip()[:220]
    if not snippet:
        snippet = "Geen nieuwe tradable headlines. (SEC- of RSS-sectie leverde niets op in deze run.)"
    return f"{src} Insider Monitor – update ({ts}): {snippet}"

def sanitize_to(raw:str)->str:
    raw = raw.replace("\u00A0","").replace("\u202F","").replace("\u2007","")
    cleaned = "".join(ch for ch in raw if ch.isdigit() or ch=="+").strip()
    if cleaned.count("+")>1: cleaned = "+"+cleaned.replace("+","")
    return cleaned

def main()->int:
    body = build_body()
    sid = os.getenv("TWILIO_ACCOUNT_SID","").strip()
    tok = os.getenv("TWILIO_AUTH_TOKEN","").strip()
    to  = sanitize_to(os.getenv("ALERT_TO_INSIDER",""))
    print("[notify] Body preview:\n", body)
    if not re.fullmatch(r"\+\d{10,15}", to):
        print("To ongeldig; verzenden overgeslagen."); return 0
    try:
        from twilio.rest import Client
        msg = Client(sid,tok).messages.create(body=body, from_="whatsapp:+14155238886", to=f"whatsapp:{to}")
        print("WhatsApp verzonden! SID:", msg.sid); return 0
    except Exception as e:
        print("Fout bij versturen:", e); return 0

if __name__=="__main__":
    raise SystemExit(main())
