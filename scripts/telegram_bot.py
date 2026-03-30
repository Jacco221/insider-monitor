#!/usr/bin/env python3
"""
Telegram Bot — Luistert naar bevestigingen en voert trades uit.

Draait continu op de achtergrond. Wanneer de portfolio monitor een advies
stuurt via Telegram, kan de gebruiker "JA" antwoorden om trades uit te voeren.

Commando's:
  JA / YES / UITVOEREN  → Voer openstaande trade-adviezen uit
  NEE / NO / SKIP        → Sla over, geen actie
  STATUS                  → Toon huidige portfolio signalen
  PORTFOLIO               → Toon DEGIRO posities

Gebruik:
  python3 scripts/telegram_bot.py                # Start bot (voorgrond)
  nohup python3 scripts/telegram_bot.py &        # Start bot (achtergrond)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path
from time import sleep
from urllib.request import Request, urlopen

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PROJECT_DIR = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python3"
PENDING_FILE = PROJECT_DIR / "data" / "state" / "pending_trades.json"


def telegram_get_updates(offset: int = 0) -> list[dict]:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
    try:
        with urlopen(Request(url), timeout=35) as r:
            data = json.loads(r.read().decode())
            return data.get("result", [])
    except Exception:
        return []


def telegram_send(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    try:
        urlopen(Request(url, data=data, method="POST"), timeout=15)
    except Exception as e:
        print(f"[warn] Telegram send fout: {e}", file=sys.stderr)


def load_pending() -> list[dict]:
    """Laad openstaande trade-adviezen uit portfolio_monitor.json."""
    monitor_file = PROJECT_DIR / "data" / "reports" / "portfolio_monitor.json"
    if not monitor_file.exists():
        return []

    results = json.loads(monitor_file.read_text(encoding="utf-8"))
    pending = []

    # Laad huidige portfolio tickers uit laatste run
    portfolio_tickers = set()
    try:
        # Lees portfolio uit DEGIRO (cached)
        pos_file = PROJECT_DIR / "data" / "reports" / "degiro" / "last_portfolio.json"
        if pos_file.exists():
            portfolio_tickers = set(json.loads(pos_file.read_text()).get("tickers", []))
    except Exception:
        pass

    for r in results:
        ticker = r["ticker"]
        signal = r["signal"]

        if signal == "EXIT" and ticker in portfolio_tickers:
            pending.append({
                "action": "SELL",
                "ticker": ticker,
                "reason": "; ".join(r.get("reasons", [])),
                "net_flow": r.get("net_flow", 0),
                "csuite": r.get("csuite_sellers", []),
            })
        elif signal == "STRONG_HOLD" and ticker not in portfolio_tickers:
            if r.get("net_flow", 0) > 0 and r.get("unique_buyers", 0) >= 2:
                pending.append({
                    "action": "BUY",
                    "ticker": ticker,
                    "reason": "; ".join(r.get("reasons", [])),
                    "net_flow": r.get("net_flow", 0),
                    "csuite": r.get("csuite_buyers", []),
                })

    return pending


def format_pending(pending: list[dict]) -> str:
    if not pending:
        return "✅ Geen openstaande trade-adviezen."

    lines = ["📋 <b>Openstaande trade-adviezen:</b>\n"]
    for p in pending:
        emoji = "🔴" if p["action"] == "SELL" else "🟢"
        lines.append(f"{emoji} <b>{p['action']} {p['ticker']}</b>")
        lines.append(f"  Netto flow: ${p['net_flow']:,.0f}")
        lines.append(f"  Reden: {p['reason'][:80]}")
        if p.get("csuite"):
            lines.append(f"  C-suite: {', '.join(p['csuite'][:3])}")
        lines.append("")

    lines.append("Antwoord <b>JA</b> om uit te voeren, <b>NEE</b> om over te slaan.")
    return "\n".join(lines)


def execute_trades():
    """Voer auto_trade.py --execute uit."""
    env = os.environ.copy()
    result = subprocess.run(
        [str(VENV_PYTHON), "scripts/auto_trade.py", "--execute", "--max-order", "500", "--max-daily", "2000"],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return result.stdout + result.stderr


def get_status():
    """Haal laatste portfolio monitor status."""
    monitor_file = PROJECT_DIR / "data" / "reports" / "portfolio_monitor.json"
    if not monitor_file.exists():
        return "Geen monitor data beschikbaar. Wacht op de volgende scan."

    results = json.loads(monitor_file.read_text(encoding="utf-8"))
    lines = ["📊 <b>Portfolio Monitor Status</b>\n"]
    for r in results:
        emoji = {"STRONG_HOLD": "🟢", "HOLD": "🟡", "EXIT": "🔴"}.get(r["signal"], "⚪")
        lines.append(f"{emoji} <b>{r['ticker']}</b> — {r['signal']}")
        lines.append(f"  Netto: ${r.get('net_flow', 0):,.0f} | Laatste buy: {r.get('days_since_buy', '?')}d")
    return "\n".join(lines)


def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("[fout] TELEGRAM_BOT_TOKEN en TELEGRAM_CHAT_ID env vars vereist", file=sys.stderr)
        raise SystemExit(1)

    print(f"[bot] Insider Monitor Telegram Bot gestart", file=sys.stderr)
    print(f"[bot] Luistert naar berichten van chat {CHAT_ID}...", file=sys.stderr)
    telegram_send("🤖 <b>Insider Monitor Bot gestart</b>\n\nCommando's:\n• <b>JA</b> — Voer trade-adviezen uit\n• <b>NEE</b> — Sla over\n• <b>STATUS</b> — Toon signalen\n• <b>PORTFOLIO</b> — Toon posities")

    offset = 0

    while True:
        updates = telegram_get_updates(offset)

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})

            # Alleen berichten van de juiste chat
            if str(msg.get("chat", {}).get("id")) != CHAT_ID:
                continue

            text = (msg.get("text") or "").strip().upper()

            if text in ("JA", "YES", "UITVOEREN", "GO", "OK"):
                pending = load_pending()
                if not pending:
                    telegram_send("✅ Geen openstaande adviezen om uit te voeren.")
                else:
                    telegram_send(f"⏳ Trades worden uitgevoerd ({len(pending)} acties)...")
                    try:
                        output = execute_trades()
                        telegram_send(f"✅ <b>Trades uitgevoerd</b>\n\n<pre>{output[-500:]}</pre>")
                    except Exception as e:
                        telegram_send(f"❌ Fout bij uitvoering: {e}")

            elif text in ("NEE", "NO", "SKIP", "OVERSLAAN"):
                telegram_send("⏭ Overgeslagen. Geen trades uitgevoerd.")

            elif text in ("STATUS", "SIGNALEN", "CHECK"):
                telegram_send(get_status())

            elif text in ("PORTFOLIO", "POSITIES", "HOLDINGS"):
                try:
                    result = subprocess.run(
                        [str(VENV_PYTHON), "scripts/degiro_trade.py", "portfolio"],
                        cwd=str(PROJECT_DIR),
                        capture_output=True, text=True,
                        env=os.environ.copy(),
                        timeout=60,
                    )
                    telegram_send(f"<pre>{result.stdout[-800:]}</pre>" if result.stdout else "Kan portfolio niet ophalen.")
                except Exception as e:
                    telegram_send(f"❌ Fout: {e}")

            elif text in ("HELP", "?"):
                telegram_send("🤖 <b>Commando's:</b>\n• <b>JA</b> — Voer trade-adviezen uit\n• <b>NEE</b> — Sla over\n• <b>STATUS</b> — Toon signalen\n• <b>PORTFOLIO</b> — Toon posities\n• <b>HELP</b> — Dit menu")

        if not updates:
            sleep(1)


if __name__ == "__main__":
    main()
