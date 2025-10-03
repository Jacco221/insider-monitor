import os, re, sys, pathlib
from datetime import datetime

BASE = pathlib.Path("data/reports")

def _find_latest_file():
    if BASE.exists():
        files = sorted(BASE.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None
    return None

def _parse_top_news(txt: str) -> list[str]:
    # 1) Probeer expliciet de sectie '== Laatste nieuws =='
    m = re.search(r"^==\s*Laatste nieuws\s*==\s*(.+?)(?:\n==|\\Z)", txt, flags=re.M|re.S)
    block = (m.group(1).strip() if m else txt)

    # 2) verzamel headline-achtige regels
    lines = [ln.strip(" •-\t") for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("==")]
    if not lines:
        # fallback: probeer een lijst onder 'News items:' (zoals in sommige rapporten)
        m2 = re.search(r"^News items:\s*(.+?)(?:\n\n|\\Z)", txt, flags=re.M|re.S|re.I)
        if m2:
            lines = [ln.strip(" •-\t") for ln in m2.group(1).splitlines() if ln.strip()]

    if not lines:
        return []

    # scoor regels op [0.87] / (0.87) / score=0.87 en bonus voor HOT
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
    # pak 1–2 beste regels, strip leading bullets/scores
    out = []
    for _, ln in scored[:2]:
        ln = re.sub(r"^\s*[-•]\s*", "", ln)
        ln = re.sub(r"^\s*(\[\s*[01]?\.\d+\s*\]|\(\s*[01]?\.\d+\s*\)|score\s*=\s*[01]?\.\d+)\s*[:\-]?\s*", "", ln, flags=re.I)
        out.append(ln)
    return out

def build_body() -> str:
    latest = BASE / "latest.txt"
    pick = latest if latest.exists() else _find_latest_file()
    if not pick or not pick.exists():
        return "Insider Monitor: nog geen rapport beschikbaar."

    txt = pick.read_text(encoding="utf-8", errors="ignore")
    bullets = _parse_top_news(txt)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    if bullets:
        return f"Insider Monitor – top nieuws ({ts}):\n• " + "\n• ".join(bullets)
    # laatste fallback: korte samenvatting
    return "Insider Monitor – samenvatting:\n" + txt[:240].strip()

def sanitize_to(raw: str) -> str:
    cleaned = raw.replace("\u00A0","").replace("\u202F","").replace("\u2007","").strip()
    cleaned = "".join(ch for ch in cleaned if ch.isdigit() or ch == "+")
    if cleaned.count("+") > 1:
        cleaned = "+" + cleaned.replace("+","")
    return cleaned

def main() -> int:
    body = build_body()
    sid = os.getenv("TWILIO_ACCOUNT_SID","").strip()
    tok = os.getenv("TWILIO_AUTH_TOKEN","").strip()
    to_raw = os.getenv("ALERT_TO_INSIDER","")
    to = sanitize_to(to_raw)

    import re
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
    raise SystemExit(main())
