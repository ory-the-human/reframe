"""Google sign-in plus Ambient device registration, shared by the CLI and web UI.

The flow blocks while the user signs in and picks albums on their phone, so the
web UI runs it in a background thread and renders its public attributes. The CLI
runs it inline and prints each step from `on_update`.
"""

import threading
import time
import uuid
from typing import Any, Callable, Optional

from .api import AmbientClient
from .auth import TokenStore, poll_for_token, start_device_flow
from ...config import Config
from ...store import read_json, write_json

SOURCE_SELECTION_TIMEOUT_S = 15 * 60


def parse_duration(value: Optional[str], default: float = 5.0) -> float:
    # pollingConfig.pollInterval is a protobuf duration string such as "3.5s".
    try:
        return float(value.rstrip("s")) if value else default
    except ValueError:
        return default


class LoginFlow:
    # Steps, in order: starting -> awaiting_signin -> registering ->
    # awaiting_albums -> done. Any step can end in "error".
    def __init__(
        self,
        cfg: Config,
        new_device: bool = False,
        on_update: Optional[Callable[["LoginFlow"], None]] = None,
    ):
        self.cfg = cfg
        self.new_device = new_device
        self.on_update = on_update
        self.step = "starting"
        self.user_code: Optional[str] = None
        self.verification_url: Optional[str] = None
        self.settings_uri: Optional[str] = None
        self.device: Optional[dict[str, Any]] = None
        self.error: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.step not in ("done", "error")

    def _set(self, step: str) -> None:
        self.step = step
        if self.on_update:
            self.on_update(self)

    def start_in_background(self) -> None:
        threading.Thread(target=self.run, daemon=True, name="login-flow").start()

    def run(self) -> None:
        try:
            self._run()
        except Exception as e:  # surfaced to the UI rather than killing the thread silently
            self.error = str(e)
            self._set("error")

    def _run(self) -> None:
        cfg = self.cfg
        existing = read_json(cfg.device_file)
        reuse_device = existing is not None and not self.new_device
        request_id = existing["request_id"] if reuse_device else str(uuid.uuid4())

        flow = start_device_flow(cfg, request_id)
        self.user_code = flow["user_code"]
        self.verification_url = flow["verification_url"]
        self._set("awaiting_signin")

        tokens = TokenStore(cfg)
        token = poll_for_token(cfg, flow)
        token["first_obtained_at"] = token["obtained_at"]
        tokens.save(token)
        self._set("registering")

        client = AmbientClient(tokens)
        if reuse_device:
            device = client.get_device(existing["id"])
        else:
            device = client.create_device(cfg.device_name, request_id)
            write_json(cfg.device_file, {"id": device["id"], "request_id": request_id})
        self.device = device
        self.settings_uri = device.get("settingsUri")

        if not device.get("mediaSourcesSet"):
            self._set("awaiting_albums")
            interval = parse_duration(device.get("pollingConfig", {}).get("pollInterval"))
            deadline = time.time() + SOURCE_SELECTION_TIMEOUT_S
            while not device.get("mediaSourcesSet"):
                if time.time() > deadline:
                    raise TimeoutError("timed out waiting for album selection; pick albums, then refresh")
                time.sleep(interval)
                device = client.get_device(device["id"])
            self.device = device

        self._set("done")
