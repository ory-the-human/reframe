"""OAuth 2.0 for TV and limited-input devices, as required by the Ambient API.

https://developers.google.com/identity/protocols/oauth2/limited-input-device
"""

import json
import time
from typing import Any

import requests

from ...config import Config
from ...store import read_json, write_json

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = "profile https://www.googleapis.com/auth/photosambient.mediaitems"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

# Refresh a little before Google's stated expiry to avoid racing it.
EXPIRY_MARGIN_S = 60


class AuthError(RuntimeError):
    pass


def start_device_flow(cfg: Config, request_id: str) -> dict[str, Any]:
    """Request a user code.

    The `state` param is the Ambient API's streamlined flow: Google creates the
    album-picking step inside the consent screen, keyed by `requestId`, which we
    then reuse when calling devices.create.
    """
    state = json.dumps({"requestId": request_id, "displayName": cfg.device_name})
    resp = requests.post(
        DEVICE_CODE_URL,
        data={"client_id": cfg.client_id, "scope": SCOPES, "state": state},
        timeout=30,
    )
    if not resp.ok:
        raise AuthError(f"device code request failed: {resp.status_code} {resp.text}")
    return resp.json()


def poll_for_token(cfg: Config, flow: dict[str, Any]) -> dict[str, Any]:
    interval = int(flow.get("interval", 5))
    deadline = time.time() + int(flow.get("expires_in", 1800))
    while time.time() < deadline:
        time.sleep(interval)
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "device_code": flow["device_code"],
                "grant_type": DEVICE_GRANT,
            },
            timeout=30,
        )
        body = resp.json()
        if resp.ok:
            return _stamp(body)
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise AuthError(f"token polling failed: {error} {body.get('error_description', '')}")
    raise AuthError("device code expired before sign-in completed")


def _stamp(token: dict[str, Any]) -> dict[str, Any]:
    token["obtained_at"] = int(time.time())
    token["expires_at"] = token["obtained_at"] + int(token.get("expires_in", 0))
    return token


class TokenStore:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def save(self, token: dict[str, Any]) -> None:
        write_json(self.cfg.token_file, token)

    def access_token(self) -> str:
        token = read_json(self.cfg.token_file)
        if token is None:
            raise AuthError("not signed in; run `cozysync login` first")
        if time.time() < token["expires_at"] - EXPIRY_MARGIN_S:
            return token["access_token"]
        return self._refresh(token)["access_token"]

    def _refresh(self, token: dict[str, Any]) -> dict[str, Any]:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
                "refresh_token": token["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if not resp.ok:
            raise AuthError(
                f"token refresh failed ({resp.status_code}): {resp.text}; "
                "if the OAuth app is in Testing mode the refresh token expires after 7 days"
            )
        # Google omits refresh_token on refresh responses; keep the original.
        fresh = _stamp(resp.json())
        fresh.setdefault("refresh_token", token["refresh_token"])
        fresh["first_obtained_at"] = token.get("first_obtained_at", token["obtained_at"])
        self.save(fresh)
        return fresh
