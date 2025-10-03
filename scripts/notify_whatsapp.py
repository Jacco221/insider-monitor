import os, re, sys, pathlib
from datetime import datetime

BASE = pathlib.Path("data/reports")

NBSPS = ["\u00A0","\u202F","\u2007","\u2060","\u2009","\u200A","\u2002","\u2003","\u2004","\u2005","\u2006","\u2008"]

def _norm(s:str) -> str:
    for ch in NBSPS: s = s.replace(ch, " ")
    return s.replace("\r\n","\n").replace("\r","\n")

def _newest_txt(exclude_latest=True):
    if not BASE.exists(): return None
    files = sorted(BASE.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if exclude_latest:
        files = [f for f in files if f.name != "latest.txt"]
    return files[0] if files else None

def _parse_top_news(txt: str) -> list[str]:
    txt = _norm(txt)

    # 1) probeer lijn-gedreven detectie van de sectie "== Laatste nieuws =="
    lines = [ln.rstrip() for ln in txt.split("\n")]
    start = None
    for i, ln in enumerate(lines):
        ln_clean = ln.strip()
        if ln_clean.startswith("==") and "Laatste nieuws" in ln_clean and ln_clean.endswith("=="):
            start = i + 1
            break

    section = []
    if start is not None:
        # neem tot aan volgende "=="-kop of einde
        for j in range(start, len(lines)):
            ln = lines[j]
            if ln.strip().startswith("=="): break
            section.append(ln)
    else:
        # fallback: heuristiek—pak regels na een eventuele "News items:" label
        m = re.search(r"^News items:\s*(.+?)(?:\n\n|\Z)", txt, flags=re.M|re.S|re.I)
        section = _norm(m.group(1)).splitlines() if m else lines

    # 2) kies **alleen bullets** als headlines
    bullets = [re.sub(r"^\s*[•\-]\s*", "", ln).strip() for ln in section
               if ln.strip().startswith(("-", "•")) and ln.strip().lstrip("•-").strip()]

    # 3) als er geen bullets zijn, laatste fallback: heuristiek (maar filter zinnen als de seed-uitleg weg)
    if not bullets:
        candidates = []
        for ln in section:
            s = ln.strip()
            if not s or s.startswith("=="): 
                continue
            # skip bekende seed-tekstjes
            if "Seed" in s and "rapport" in s:
                continue
            candidates.append(s)
        bullets = candidates

    # 4) scoor (herken [0.92]/(0.92)/score=0.92, bonus voor HOT); kies top 2
    scored = []
    for ln in bullets:
        mm = re.search(r"(?:\[\s*([01]?\.\d+)\s*\]|\(\s*([01]?\.\d+)\s*\)|score\s*=\s*([01]?\.\d+))", ln, flags=re.I)
        score = 0.0
        if mm:
            for g in (mm.group(1), mm.group(2), mm.group(3)):
                if g: score = float(g); break
        if re.search(r"\bHOT\b", ln, flags=re.I):
            score += 0.1
        scored.append((-(score), ln))
    scored.sort()
    return [ln for _, ln in scored[:2]]

def _pick_body_and_source():
    latest = BASE / "latest.txt"
    seed   = BASE / "seed_latest.txt"
    source = None
    if latest.exists() and latest.stat().st_size > 0:
        source = latest
    else:
        src = _newest_txt()
        if src: source = src
        elif seed.exists(): source = seed
    if not source:
        return "Insider Monitor: geen rapporten gevonden.", None
    txt = source.read_text(encoding="utf-8", errors="ignore")
    bullets = _parse_top_news(txt)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    if bullets:
        body = f"Insider Monitor – top nieuws ({ts}):\n• " + "\n• ".join(bullets)
    else:
        # compacte snippet
        snippet = re.sub(r"\s+", " ", _norm(txt)).strip()[:240]
        body = f"Insider Monitor – samenvatting ({ts}):\n{snippet}"
    return body, source

def _sanitize_to(raw: str) -> str:
    raw = _norm(raw)
    cleaned = "".join(ch for ch in raw if ch.isdigit() or ch == "+").strip()
    if cleaned.count("+") > 1:
        cleaned = "+" + cleaned.replace("+","")
    return cleaned

def main() -> int:
    body, used = _pick_body_and_source()
    print(f"[notify] using source: {used if used else '∅'}")
    sid = os.getenv("TWILIO_ACCOUNT_SID","").strip()
    tok = os.getenv("TWILIO_AUTH_TOKEN","").strip()
    to  = _sanitize_to(os.getenv("ALERT_TO_INSIDER",""))
    if not re.fullmatch(r"\+\d{10,15}", to):
        print("WAARSCHUWING: ALERT_TO_INSIDER ongeldig; versturen overgeslagen.", file=sys.stderr)
        print("Body (zou verstuurd worden):\n", body); return 0
    try:
        from twilio.rest import Client
        msg = Client(sid, tok).messages.create(
            body=body, from_="whatsapp:+14155238886", to=f"whatsapp:{to}"
        )
        print("WhatsApp verzonden! SID:", msg.sid); return 0
    except Exception as e:
        print("WAARSCHUWING: WhatsApp verzenden faalde:", repr(e), file=sys.stderr)
        print("Body (zou verstuurd worden):\n", body); return 0

if __name__ == "__main__":
    import re
    raise SystemExit(main())
