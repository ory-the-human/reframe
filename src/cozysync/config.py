import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import read_json, write_json

# Settings editable from the web UI. Values saved there live in
# /data/settings.json and take precedence over the environment, which only
# provides defaults.
SETTINGS = {
    "source": ("SOURCE", "google_ambient"),
    "frame": ("FRAME", "cozyla_adb"),
    "frame_folder": ("FRAME_FOLDER", "/frame"),
    "client_id": ("GOOGLE_CLIENT_ID", ""),
    "client_secret": ("GOOGLE_CLIENT_SECRET", ""),
    "device_name": ("DEVICE_NAME", "Cozyla frame"),
    "frame_address": ("FRAME_ADDRESS", "192.168.1.50:5555"),
    # How photos are shown, for every frame that runs our slideshow. Each screen
    # can override these in its slideshow URL.
    "display_interval": ("DISPLAY_INTERVAL", "15"),
    "display_layout": ("DISPLAY_LAYOUT", "single"),
    "display_orientation": ("DISPLAY_ORIENTATION", "auto"),
    "display_shuffle": ("DISPLAY_SHUFFLE", "yes"),
}

DISPLAY_CHOICES = {
    "display_layout": {"single": "One photo at a time", "pair": "Two photos side by side when they fit better"},
    "display_orientation": {
        "auto": "Follow the screen",
        "landscape": "Landscape",
        "portrait": "Portrait",
        "landscape-flipped": "Landscape, upside down",
        "portrait-flipped": "Portrait, upside down",
    },
    "display_shuffle": {"yes": "Shuffle", "no": "In order"},
}


@dataclass(frozen=True)
class Config:
    source: str
    frame: str
    frame_folder: str
    client_id: str
    client_secret: str
    data_dir: Path
    device_name: str
    frame_address: str
    display_interval: str
    display_layout: str
    display_orientation: str
    display_shuffle: str

    @property
    def token_file(self) -> Path:
        return self.data_dir / "token.json"

    @property
    def device_file(self) -> Path:
        return self.data_dir / "device.json"

    @property
    def display(self) -> dict[str, object]:
        """Display settings, validated, as the slideshow page uses them."""
        try:
            interval = max(3, min(3600, int(self.display_interval)))
        except ValueError:
            interval = 15
        values = {"interval": interval}
        for key in ("display_layout", "display_orientation", "display_shuffle"):
            value = getattr(self, key)
            choices = DISPLAY_CHOICES[key]
            values[key[len("display_"):]] = value if value in choices else next(iter(choices))
        values["shuffle"] = values["shuffle"] == "yes"
        return values

    @property
    def has_client(self) -> bool:
        return bool(self.client_id and self.client_secret)


def data_dir() -> Path:
    path = Path(os.environ.get("DATA_DIR", "/data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return data_dir() / "settings.json"


def load() -> Config:
    saved = read_json(settings_file()) or {}
    values = {key: saved.get(key) or os.environ.get(env, default) for key, (env, default) in SETTINGS.items()}
    return Config(data_dir=data_dir(), **values)


def save_settings(updates: dict[str, Any]) -> None:
    saved = read_json(settings_file()) or {}
    saved.update({k: v for k, v in updates.items() if k in SETTINGS})
    write_json(settings_file(), saved)
