import os, re, sys, pathlib

def mask(s: str) -> str:
    if not s: return "∅"
    return s[:3] + "…" + s[-2:] if len(s) > 5 else s

def sanitize_to(raw: str) -> str:
    # Verwijder verborgen spaties (NBSP, NNBSP, NARROW NBSP, etc.) en gewone whitespace
    cleaned = raw.replace("\u00A0","").replace("\u202F","").replace("\u2007","").strip()
    # Houd alleen '+' en digits over
    cleaned = "".join(ch for ch in cleaned if ch.isdigit() or ch == "+")
    # Strip dubbele plussen etc.
    if cleaned.count("+") > 1:
        cleaned = "+" + cleaned.replace("+","")
    return cleaned

def pick_top_news(txt: str) -> str:
    m = re.search(r"^==\s*Laatste nieuws\s*==\s*(.+?)(?:\n==|\\Z)", txt, flags=re.M|re.S)
    block = (m.group(1).strip() if m else txt)
    lines = [ln.strip(" •-\t") for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("==")]
    if not lines:
        return "Insider Monitor – geen nieuwsregels gevonden."
    scored = []
    for ln in lines:
        mm = re.search(r"(?:\[\s*([01]?\.\d+)\s*\]|\(\s*([01]?\.\d+)\s*\)|score\s*=\s*([01]?\.\d+))", ln, flags=re.I)
        score = 0.0
        if mm:
            for g in (mm.group(1), mm.group(2), mm.group(3)):
                if g: score = float(g); break
        if re.search(r"\bHOT\b", ln, flags=re.I): score += 0.1
        scored.append((-(score), ln))
    scored.sort()
    top = [ln for _, ln in scored[:2]]
    # verwijder leading score/bullet noise
    clean = []
    for ln in top:
        ln = re.sub(r"^\s*[-•]\s*", "", ln)
        ln = re.sub(r"^\s*(\[\s*[01]?\.\d+\s*\]|\(\s*[01]?\.\d+\s*\)|score\s*=\s*[01]?\.\d+)\s*[:\-]?\s*", "", ln, flags=re.I)
        clean.append(ln)
    return "Insider Monitor – top nieuws:\n• " + "\n• ".join(clean)

def build_body() -> str:
    latest = pathlib.Path("data/reports/latest.txt")
    if not latest.exists():
        return "Insider Monitor: nog geen rapport beschikbaar."
    txt = latest.read_text(encoding="utf-8", errors="ignore")
    return pick_top_news(txt)

def main() -> int:
    body = build_body()

    raw_to = os.getenv("ALERT_TO_INSIDER","")
    to = sanitize_to(raw_to)

    # Debug (masked)
    codepoints = [ord(c) for c in raw_to]
    print(f"[notify] ALERT_TO_INSIDER raw (masked): {mask(raw_to)}")
    print(f"[notify] raw len: {len(raw_to)} codepoints: {codepoints}")
    print(f"[notify] sanitized (masked): {mask(to)}")

    sid = os.getenv("TWILIO_ACCOUNT_SID","").strip()
    tok = os.getenv("TWILIO_AUTH_TOKEN","").strip()

    # Valideer E.164
    if not re.fullmatch(r"\+\d{10,15}", to):
        print("WAARSCHUWING: ALERT_TO_INSIDER ongeldig na sanitisatie; versturen overgeslagen.", file=sys.stderr)
        print("Body (zou verstuurd worden):\n", body)
        return 0  # run mag nooit falen

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
