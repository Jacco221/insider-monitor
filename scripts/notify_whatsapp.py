import os, re, sys, pathlib
from datetime import datetime

BASE = pathlib.Path("data/reports")

def _newest_txt(exclude_latest=True):
    if not BASE.exists(): return None
    files = sorted(BASE.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if exclude_latest:
        files = [f for f in files if f.name != "latest.txt"]
    return files[0] if files else None

def _parse_top_news(txt: str) -> list[str]:
    m = re.search(r"^==\s*Laatste nieuws\s*==\s*(.+?)(?:\n==|\\Z)", txt, flags=re.M|re.S)
    block = (m.group(1).strip() if m else txt)
    lines = [ln.strip(" •-\t") for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("==")]
    if not lines:
        m2 = re.search(r"^News items:\s*(.+?)(?:\n\n|\\Z)", txt, flags=re.M|re.S|re.I)
        if m2:
            lines = [ln.strip(" •-\t") for ln in m2.group(1).splitlines() if ln.strip()]
    if not lines: return []
    scored = []
    for ln in lines:
        mm = re.search(r"(?:\[\s*([01]?\.\d+)\s*\]|\(\s*([01]?\.\d+)\s*\)|score\s*=\s*([01]?\.\d+))", ln, flags=re.I)
        score = 0.0
        if mm:
            for g in (mm.group(1), mm.group(2), mm.group(3)):
                if g: score = float(g); break
        if re.search(r"\bHOT\b", ln, flags=re.I):
            score += 0.1
        scored.append((-(score), ln))
    scored.sort()
    out = []
    for _, ln in scored[:2]:
        ln = re.sub(r"^\s*[-•]\s*", "", ln)
        ln = re.sub(r"^\s*(\[\s*[01]?\.\d+\s*\]|\(\s*[01]?\.\d+\s*\)|score\s*=\s*[01]?\.\d+)\s*[:\-]?\s*", "", ln, flags=re.I)
        out.append(ln)
    return out

def _pick_body_and_source():
    # volgorde: latest.txt > nieuwste andere .txt > seed_latest.txt
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
        snippet = re.sub(r"\s+", " ", txt).strip()[:240]
        body = f"Insider Monitor – samenvatting ({ts}):\n{snippet}"
    return body, source

def _sanitize_to(raw: str) -> str:
    cleaned = raw.replace("\u00A0","").replace("\u202F","").replace("\u2007","").strip()
    cleaned = "".join(ch for ch in cleaned if ch.isdigit() or ch == "+")
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
        print("Body (zou verstuurd worden):\n", body)
        return 0

    try:
        from twilio.rest import Client
        client = Client(sid, tok)
        msg = client.messages.create(
            body=body,
            from_="whatsapp:+14155238886",
            to=f"whatsapp:{to}",
        )
        print("WhatsApp verzonden! SID:", msg.sid)
        return 0
    except Exception as e:
        print("WAARSCHUWING: WhatsApp verzenden faalde:", repr(e), file=sys.stderr)
        print("Body (zou verstuurd worden):\n", body)
        return 0

if __name__ == "__main__":
    import re
    raise SystemExit(main())
