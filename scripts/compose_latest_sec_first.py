from pathlib import Path

REP    = Path("data/reports"); REP.mkdir(parents=True, exist_ok=True)
SEC    = REP / "sec_headlines.txt"
RANKED = REP / "sec_headlines_ranked.txt"
LAT    = REP / "latest.txt"

MAX_ITEMS = 15  # hoeveel bullets naar WA

def read_lines(p: Path, n: int) -> list[str]:
    if not p.exists() or p.stat().st_size <= 0:
        return []
    raw = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    # alleen niet-lege regels
    lines = [l.strip() for l in raw if l.strip()]
    return lines[:n]

def main() -> int:
    # kies bron: ranked als hij > 0 bytes is, anders sec
    src = RANKED if (RANKED.exists() and RANKED.stat().st_size > 10) else SEC
    lines = read_lines(src, MAX_ITEMS)

    if lines:
        body = "== Laatste nieuws ==\n" + "\n".join(lines) + "\n"
    else:
        body = "== Laatste nieuws ==\n- [SEC] Geen nieuwe Form 4-headlines in de laatste run.\n"

    LAT.write_text(body, encoding="utf-8")
    print(f"[compose] latest.txt written from {src.name}; items: {len(lines)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
