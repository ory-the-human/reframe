import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import read_json, write_json

# Settings editable from the web UI. Values saved there live in
# /data/settings.json and take precedence over the environment, which only
# provides defaults. Frames have their own file (frames/registry.py).
SETTINGS = {
    "source": ("SOURCE", "google_ambient"),
    "client_id": ("GOOGLE_CLIENT_ID", ""),
    "client_secret": ("GOOGLE_CLIENT_SECRET", ""),
    "device_name": ("DEVICE_NAME", "cozysync"),
    # How frames reach this server, e.g. http://192.168.1.10:1992, for the
    # slideshow link written into kiosk frames.
    "public_url": ("PUBLIC_URL", ""),
    # Default display settings for frames that run the cozysync slideshow.
    # Each frame can override them, and each screen can override them again
    # in its slideshow URL.
    "display_interval": ("DISPLAY_INTERVAL", "15"),
    "display_layout": ("DISPLAY_LAYOUT", "pair"),
    "display_orientation": ("DISPLAY_ORIENTATION", "auto"),
    "display_shuffle": ("DISPLAY_SHUFFLE", "yes"),
    "display_fill": ("DISPLAY_FILL", "blur"),
}

DISPLAY_KEYS = ("display_interval", "display_layout", "display_orientation", "display_shuffle", "display_fill")

DISPLAY_CHOICES = {
    "display_layout": {"pair": "Two photos together when they fit better", "single": "One photo at a time"},
    "display_orientation": {
        "auto": "Follow the screen",
        "landscape": "Landscape",
        "portrait": "Portrait",
        "landscape-flipped": "Landscape, upside down",
        "portrait-flipped": "Portrait, upside down",
    },
    "display_shuffle": {"yes": "Shuffle", "no": "In order"},
    "display_fill": {"blur": "A blurred copy of the photo", "black": "Black"},
}


def display_values(raw: dict[str, str]) -> dict[str, object]:
    """Validate display_* strings into what the slideshow page uses."""
    try:
        interval = max(3, min(3600, int(raw.get("display_interval", ""))))
    except ValueError:
        interval = 15
    values: dict[str, object] = {"interval": interval}
    for key, choices in DISPLAY_CHOICES.items():
        value = raw.get(key, "")
        values[key[len("display_"):]] = value if value in choices else next(iter(choices))
    values["shuffle"] = values["shuffle"] == "yes"
    return values


@dataclass(frozen=True)
class Config:
    source: str
    client_id: str
    client_secret: str
    data_dir: Path
    device_name: str
    public_url: str
    display_interval: str
    display_layout: str
    display_orientation: str
    display_shuffle: str
    display_fill: str

    @property
    def token_file(self) -> Path:
        return self.data_dir / "token.json"

    @property
    def device_file(self) -> Path:
        return self.data_dir / "device.json"

    @property
    def display_raw(self) -> dict[str, str]:
        return {key: getattr(self, key) for key in DISPLAY_KEYS}

    @property
    def display(self) -> dict[str, object]:
        """Default display settings, validated."""
        return display_values(self.display_raw)

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
