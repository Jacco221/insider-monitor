#!/usr/bin/env python3
"""DEGIRO authenticatie module met in-app bevestiging en sessie-caching.

Gebruikt raw HTTP voor login (in-app flow) en degiro-connector v3 voor trading API.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from time import sleep

import requests as http_requests

STATE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "state"
SESSION_FILE = STATE_DIR / "degiro_session.json"

LOGIN_URL = "https://trader.degiro.nl/login/secure/login"
CONFIG_URL = "https://trader.degiro.nl/login/secure/config"
PA_URL = "https://trader.degiro.nl/pa/secure/client"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Origin": "https://trader.degiro.nl",
    "Referer": "https://trader.degiro.nl/login/nl",
    "Content-Type": "application/json",
}


def _get_credentials() -> tuple:
    username = os.getenv("DEGIRO_USERNAME", "")
    password = os.getenv("DEGIRO_PASSWORD", "")
    if not username or not password:
        print("[fout] DEGIRO_USERNAME en DEGIRO_PASSWORD env vars zijn vereist", file=sys.stderr)
        raise SystemExit(1)
    return username, password


def _save_session(data: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(data), encoding="utf-8")


def _load_session() -> dict:
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            pass
    return {}


def _login_raw(username: str, password: str, timeout: int = 120) -> str:
    """Login bij DEGIRO met in-app bevestiging via raw HTTP. Returns session_id."""

    session = http_requests.Session()
    session.headers.update(BROWSER_HEADERS)

    payload = {
        "username": username,
        "password": password,
        "isPassCodeReset": False,
        "isRedirectToMobile": False,
        "queryParams": {},
    }

    # Stap 1: Initieer login
    r = session.post(LOGIN_URL, json=payload)

    if r.status_code == 503 or not r.text.strip().startswith("{"):
        print(f"[fout] DEGIRO blokkeert verbinding (status {r.status_code})", file=sys.stderr)
        raise SystemExit(1)

    data = r.json()

    # Directe login
    if "sessionId" in data:
        return data["sessionId"]

    # Bad credentials
    if data.get("status") == 3:
        remaining = data.get("remainingAttempts", "?")
        print(f"[fout] Onjuiste credentials. Nog {remaining} pogingen over.", file=sys.stderr)
        raise SystemExit(1)

    # 2FA TOTP vereist
    if data.get("status") == 6:
        totp_secret = os.getenv("DEGIRO_TOTP_SECRET")
        if not totp_secret:
            print("[fout] 2FA is ingeschakeld. Stel DEGIRO_TOTP_SECRET in.", file=sys.stderr)
            raise SystemExit(1)
        import onetimepass as otp
        one_time_password = str(otp.get_totp(totp_secret))
        payload["oneTimePassword"] = one_time_password
        r2 = session.post(LOGIN_URL + "/totp", json=payload)
        d2 = r2.json()
        if "sessionId" in d2:
            return d2["sessionId"]
        print(f"[fout] TOTP login mislukt: {d2}", file=sys.stderr)
        raise SystemExit(1)

    # In-app bevestiging (status 12)
    if data.get("status") == 12:
        in_app_token = data.get("inAppToken", "")
        print(f"\n[degiro] Open je DEGIRO app en bevestig de login (token: {in_app_token})", file=sys.stderr)
        print(f"[degiro] Bevestig EENMALIG en wacht...\n", file=sys.stderr)

        # Gebruik de totp endpoint met het in-app token als oneTimePassword
        totp_payload = dict(payload)
        totp_payload["oneTimePassword"] = in_app_token

        elapsed = 0
        interval = 3
        while elapsed < timeout:
            sleep(interval)
            elapsed += interval

            # Na in-app bevestiging, probeer de originele login opnieuw
            # DEGIRO onthoudt de bevestiging server-side
            try:
                r_check = session.post(LOGIN_URL, json=payload)
                d_check = r_check.json()

                if "sessionId" in d_check:
                    print(f"[degiro] Login bevestigd!", file=sys.stderr)
                    return d_check["sessionId"]

                # Status 12 = nog niet bevestigd, maar dit genereert een nieuwe challenge
                # Status 3 = nog in afwachting
                if d_check.get("status") in (12, 3):
                    if elapsed % 15 == 0:
                        print(f"[degiro] Wacht op bevestiging... ({elapsed}s)", file=sys.stderr)
                    continue

            except Exception:
                continue

        print("[fout] Timeout bij wachten op in-app bevestiging", file=sys.stderr)
        raise SystemExit(1)

    print(f"[fout] Onverwachte login response: {data}", file=sys.stderr)
    raise SystemExit(1)


def get_trading_api():
    """Maak verbinding met DEGIRO. Returns een TradingAPI instance.

    Probeert eerst gecachte sessie, anders nieuwe login.
    """
    from degiro_connector.trading.api import API as TradingAPI
    from degiro_connector.trading.models.trading_pb2 import Credentials

    username, password = _get_credentials()
    int_account = os.getenv("DEGIRO_INT_ACCOUNT")

    kwargs = {"username": username, "password": password}
    if int_account:
        kwargs["int_account"] = int(int_account)

    credentials = Credentials(**kwargs)
    trading_api = TradingAPI(credentials=credentials)

    # Probeer gecachte sessie
    cached = _load_session()
    if cached and cached.get("session_id"):
        trading_api.connection_storage.session_id = cached["session_id"]
        try:
            trading_api.get_account_info()
            print("[degiro] Verbonden via gecachte sessie", file=sys.stderr)
            return trading_api
        except Exception:
            print("[degiro] Gecachte sessie verlopen, nieuwe login...", file=sys.stderr)

    # Nieuwe login (raw HTTP voor in-app flow)
    session_id = _login_raw(username, password)
    trading_api.connection_storage.session_id = session_id

    _save_session({"session_id": session_id})
    print(f"[degiro] Verbonden (sessie: {session_id[:8]}...)", file=sys.stderr)

    return trading_api
