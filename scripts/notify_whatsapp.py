import os, re, sys, subprocess, pathlib
from datetime import datetime

BASE = pathlib.Path("data/reports")
BASE.mkdir(parents=True, exist_ok=True)

HOT_WORDS = {"plunge","tumbles","crash","collapse","halts","investigation","probe","fraud",
             "lawsuit","downgrade","withdraws guidance","cuts guidance","misses","short seller",
             "bankruptcy","chapter 11","restatement","data breach","hack","recall","sec charges",
             "whistleblower","criminal","allegation","sanction","beats","surge","spikes","soars",
             "upgrade","raises guidance","acquisition","buyout","takeover","buyback","special dividend",
             "partnership","approval","fda approves","sec filing","form 4","insider buys","insider purchase"}

def _norm(s:str) -> str:
    return s.replace("\r\n","\n").replace("\r","\n")

def _looks_minimal(txt: str) -> bool:
    low = txt.strip().lower()
    if not low: return True
    has_header = "== laatste nieuws ==" in low
    bullets = len(re.findall(r"(?m)^\s*[-•]\s+\S+", txt))
    return has_header and bullets == 0

def _parse_top_news(txt: str):
    txt = _norm(txt)
    lines = [ln.rstrip() for ln in txt.split("\n")]
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

def _ensure_rss_if_needed(latest_path: pathlib.Path) -> str:
    txt = latest_path.read_text(encoding="utf-8", errors="ignore") if latest_path.exists() else ""
    if _looks_minimal(txt):
        try:
            subprocess.run([sys.executable, "scripts/fetch_headlines_rss.py"], check=False)
            txt = latest_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
    return txt

def build_body():
    latest = BASE/"latest.txt"
    txt = _ensure_rss_if_needed(latest)
    if not txt:
        return "[SEED] Insider Monitor – geen rapport gevonden."
    src = _detect_source(txt)
    items = _parse_top_news(txt)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    if items:
        out = []
        for ln in items[:2]:
            tag = _hot_tag(ln)
            out.append(f"{tag}{ln}")
        return f"{src} Insider Monitor – top nieuws ({ts}):\n• " + "\n• ".join(out)
    # fallback: altijd tekst sturen, nooit alleen header
    snippet = re.sub(r"\s+"," ", _norm(txt)).strip()[:220]
    if (not snippet) or (snippet.lower().strip() in {"== laatste nieuws ==", "laatste nieuws"}):
        snippet = "Geen nieuwe tradable headlines; aangevuld met RSS of bronnen waren leeg."
    return f"{src} Insider Monitor – update ({ts}): {snippet}"

def _normalize_to(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw: return ""
    # sta zowel +316… als whatsapp:+316… toe
    if raw.startswith("whatsapp:"):
        return raw
    # zet 0031… om naar +31…
    if raw.startswith("003"):
        raw = "+" + raw[2:]
    if raw.startswith("+"):
        return f"whatsapp:{raw}"
    # als iemand alleen cijfers geeft, probeer er + voor te zetten (niet ideaal, maar handig)
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits:
        return f"whatsapp:+{digits}"
    return ""

def main()->int:
    body = build_body()
    print("[notify] Body preview:\n", body)

    sid = os.getenv("TWILIO_ACCOUNT_SID","").strip()
    tok = os.getenv("TWILIO_AUTH_TOKEN","").strip()
    to_env = os.getenv("ALERT_TO_INSIDER") or os.getenv("WHATSAPP_TO") or os.getenv("ALERT_TO") or ""
    to = _normalize_to(to_env)
    if not re.fullmatch(r"whatsapp:\+\d{7,15}", to or ""):
        print(f"To ongeldig; verzenden overgeslagen. ({to_env!r})"); return 0

    from_num = os.getenv("TWILIO_WHATSAPP_FROM","whatsapp:+14155238886")
    try:
        from twilio.rest import Client
        msg = Client(sid,tok).messages.create(body=body, from_=from_num, to=to)
        print("WhatsApp verzonden! SID:", msg.sid); return 0
    except Exception as e:
        print("Fout bij versturen:", e); return 0

if __name__=="__main__":
    raise SystemExit(main())
