#!/usr/bin/env python3
"""DEGIRO authenticatie (degiro-connector v3.0.35) met in-app bevestiging en sessie-caching."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from time import sleep

from degiro_connector.core.exceptions import DeGiroConnectionError
from degiro_connector.trading.api import API as TradingAPI
from degiro_connector.trading.models.credentials import build_credentials

STATE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "state"
SESSION_FILE = STATE_DIR / "degiro_session.json"


def _save_session(session_id: str):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")


def _load_session() -> str | None:
    if SESSION_FILE.exists():
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            return data.get("session_id")
        except Exception:
            pass
    return None


def _wait_for_in_app(trading_api: TradingAPI, in_app_token: str, timeout: int = 120) -> bool:
    """Wacht op in-app bevestiging in de DEGIRO app."""
    trading_api.credentials.in_app_token = in_app_token
    print(f"\n[degiro] Open je DEGIRO app en bevestig de login (token: {in_app_token})", file=sys.stderr)

    elapsed = 0
    interval = 5
    while elapsed < timeout:
        sleep(interval)
        elapsed += interval
        try:
            trading_api.connect()
            print("[degiro] Login bevestigd!", file=sys.stderr)
            return True
        except DeGiroConnectionError as e:
            if e.error_details and e.error_details.status == 3:
                # Nog niet bevestigd, blijf wachten
                if elapsed % 15 == 0:
                    print(f"[degiro] Wacht op bevestiging... ({elapsed}s)", file=sys.stderr)
                continue
            raise
    return False


def get_trading_api() -> TradingAPI:
    """Maak verbinding met DEGIRO. Probeert gecachte sessie, anders nieuwe login.

    Vereist env vars: DEGIRO_USERNAME, DEGIRO_PASSWORD
    Optioneel: DEGIRO_INT_ACCOUNT, DEGIRO_TOTP_SECRET
    """
    username = os.getenv("DEGIRO_USERNAME", "")
    password = os.getenv("DEGIRO_PASSWORD", "")
    int_account = os.getenv("DEGIRO_INT_ACCOUNT")
    totp_secret = os.getenv("DEGIRO_TOTP_SECRET")

    if not username or not password:
        print("[fout] DEGIRO_USERNAME en DEGIRO_PASSWORD env vars zijn vereist", file=sys.stderr)
        raise SystemExit(1)

    # Build credentials
    cred_override: dict = {"username": username, "password": password}
    if int_account:
        cred_override["int_account"] = int(int_account)
    if totp_secret:
        cred_override["totp_secret_key"] = totp_secret

    credentials = build_credentials(override=cred_override)
    trading_api = TradingAPI(credentials=credentials)

    # Probeer gecachte sessie
    cached_sid = _load_session()
    if cached_sid:
        trading_api.connection_storage.session_id = cached_sid
        try:
            trading_api.get_account_info()
            print("[degiro] Verbonden via gecachte sessie", file=sys.stderr)
            return trading_api
        except Exception:
            print("[degiro] Gecachte sessie verlopen, nieuwe login...", file=sys.stderr)

    # Nieuwe login
    try:
        trading_api.connect()
    except DeGiroConnectionError as e:
        if e.error_details and e.error_details.status == 12:
            # In-app bevestiging nodig
            if not _wait_for_in_app(trading_api, e.error_details.in_app_token):
                print("[fout] Timeout bij wachten op in-app bevestiging", file=sys.stderr)
                raise SystemExit(1)
        elif e.error_details and e.error_details.status == 6:
            print("[fout] 2FA TOTP vereist. Stel DEGIRO_TOTP_SECRET in.", file=sys.stderr)
            raise SystemExit(1)
        else:
            print(f"[fout] DEGIRO login mislukt: {e}", file=sys.stderr)
            raise SystemExit(1)
    except ConnectionError as e:
        print(f"[fout] Verbindingsfout: {e}", file=sys.stderr)
        raise SystemExit(1)

    session_id = trading_api.connection_storage.session_id
    _save_session(session_id)
    print(f"[degiro] Verbonden (sessie: {session_id[:8]}...)", file=sys.stderr)

    return trading_api
