# Insider Monitor

## Wat dit project doet
SEC Form 4 insider trading monitor die automatisch insider buying/selling signalen detecteert, analyseert en via Telegram advies geeft voor portfolio herbalancering. Handelt in DEGIRO.

## Architectuur
```
Discovery (SEC ATOM feed) → Deep Dive (270d per ticker) → Portfolio Monitor → Telegram advies
```

Draait 2x per dag (09:00 + 18:00 werkdagen) via crontab.

## Huidige portfolio (DEGIRO)
- BH (Biglari Holdings) — CEO koopt actief
- BORR (Borr Drilling) — Director cluster
- GO (Grocery Outlet) — CEO + directors
- HTGC (Hercules Capital) — C-suite cluster (CEO+CFO+COO)
- LOAR (Loar Holdings) — Director cluster $14.3M

## Kernscripts
- `scripts/discovery_3bd_openmarket_ps100k.py` — SEC scan, filtert op P-code >$100k, IPO-filter, 10% owner filter
- `scripts/portfolio_deepdive_270d.py` — 270 dagen insider analyse per ticker
- `scripts/portfolio_monitor.py` — EXIT/HOLD/STRONG_HOLD signalen met rol-gewogen scoring
- `scripts/auto_trade.py` — Trade advies (dry-run in scheduler, execute na Telegram bevestiging)
- `scripts/telegram_bot.py` — Luistert naar JA/NEE/STATUS/PORTFOLIO commando's
- `scripts/run_monitor.sh` — Dagelijkse scheduler (crontab), bundelt discovery → deep dive → monitor → advies
- `scripts/degiro/` — DEGIRO API modules (auth, portfolio, orders, ticker_map)

## Signaal logica
- **STRONG_HOLD**: Vers buy signaal (<30d), C-suite koopt
- **HOLD**: Buy signaal actief (30-90d)
- **EXIT**: >90 dagen geen buy, of netto sell, of C-suite verkoopt
- Rol-gewogen: CEO/CFO (5x) > Director (2x) > 10% Owner (1x)
- IPO filter: bedrijven <1 jaar genoteerd worden overgeslagen
- Alleen code P (purchase) en S (sale), geen A/F/M/G

## Tech stack
- Python 3.12 (venv in .venv/)
- degiro-connector v3.0.35 (main branch van GitHub)
- DEGIRO login vereist in-app bevestiging (geen TOTP)
- Telegram bot voor alerts
- Credentials in .env (chmod 600)

## Env vars (in .env)
- DEGIRO_USERNAME, DEGIRO_PASSWORD, DEGIRO_INT_ACCOUNT
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
- SEC_USER_AGENT
