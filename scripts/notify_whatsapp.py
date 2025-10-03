import os, re, sys, pathlib
from datetime import datetime

def pick_top_news(txt: str) -> str:
    """
    Zoekt in latest.txt naar de beste headline:
    - Neemt eerst de sectie '== Laatste nieuws ==' als die bestaat
    - Herkent scores als [0.87], (0.87), score=0.87
    - Kiest hoogste score; valt terug op eerste niet-lege headline
    - Pakt max 2 regels
    """
    # 1) beperk tot sectie 'Laatste nieuws' als aanwezig
    m = re.search(r"^==\s*Laatste nieuws\s*==\s*(.+?)(?:\n==|\\Z)", txt, flags=re.M|re.S)
    block = (m.group(1).strip() if m else txt)

    lines_raw = [ln.strip() for ln in block.splitlines()]
    # filter lege/technische regels
    lines = [ln.strip(" •-\t") for ln in lines_raw if ln.strip() and not ln.strip().startswith("==")]

    if not lines:
        return "Insider Monitor – geen nieuwsregels gevonden."

    scored = []
    for ln in lines:
        # score in [0.87] of (0.87) of score=0.87
        mm = re.search(r"(?:\[\s*([01]?\.\d+)\s*\]|\(\s*([01]?\.\d+)\s*\)|score\s*=\s*([01]?\.\d+))", ln, flags=re.I)
        score = float(next((g for g in (mm.group(1) if mm else None, mm.group(2) if mm else None, mm.group(3) if mm else None) if g), "0") or 0)
        # prefer lines tagged HOT
        hot_bonus = 0.1 if re.search(r"\bHOT\b", ln, flags=re.I) else 0.0
        scored.append((-(score + hot_bonus), ln, score))

    scored.sort()  # hoogste (score+bonus) eerst
    top = scored[:2]

    bullets = []
    for _, ln, sc in top:
        # kort opschonen van leading bullets/scores
        ln_clean = re.sub(r"^\s*[-•]\s*", "", ln)
        ln_clean = re.sub(r"^\s*(\[\s*[01]?\.\d+\s*\]|\(\s*[01]?\.\d+\s*\)|score\s*=\s*[01]?\.\d+)\s*[:\-]?\s*", "", ln_clean, flags=re.I)
        bullets.append(ln_clean)

    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return "Insider Monitor – top nieuws (" + ts + "):\n• " + "\n• ".join(bullets)

def build_body() -> str:
    base = pathlib.Path("data/reports")
    latest = base / "latest.txt"
    if not latest.exists():
        return "Insider Monitor: nog geen rapport beschikbaar."
    txt = latest.read_text(encoding="utf-8", errors="ignore")
    return pick_top_news(txt)

def main() -> int:
    body = build_body()

    sid = os.getenv("TWILIO_ACCOUNT_SID","").strip()
    tok = os.getenv("TWILIO_AUTH_TOKEN","").strip()
    to  = os.getenv("ALERT_TO_INSIDER","").strip()

    # Validatie: E.164
    if not re.fullmatch(r"\+\d{10,15}", to):
        print("WAARSCHUWING: ALERT_TO_INSIDER ongeldig; versturen overgeslagen.", file=sys.stderr)
        print("Body (zou verstuurd worden):\n", body)
        return 0  # nooit job breken

    try:
        from twilio.rest import Client
        client = Client(sid, tok)
        msg = client.messages.create(
            body=body,
            from_="whatsapp:+14155238886",  # Twilio WhatsApp sandbox sender
            to=f"whatsapp:{to}",
        )
        print("WhatsApp verzonden! SID:", msg.sid)
        return 0
    except Exception as e:
        print("WAARSCHUWING: WhatsApp verzenden faalde:", repr(e), file=sys.stderr)
        print("Body (zou verstuurd worden):\n", body)
        return 0  # nooit job breken

if __name__ == "__main__":
    raise SystemExit(main())
