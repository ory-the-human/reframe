import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .store import read_json, write_json

# Settings editable from the web UI. Values saved there live in
# /data/settings.json and take precedence over the environment, which only
# provides defaults. Frames have their own file (frames/registry.py).
SETTINGS = {
    "source": ("SOURCE", "google_ambient"),
    "client_id": ("GOOGLE_CLIENT_ID", ""),
    "client_secret": ("GOOGLE_CLIENT_SECRET", ""),
    "device_name": ("DEVICE_NAME", "reframe"),
    # How frames reach this server, e.g. http://192.168.1.10:1992, for the
    # slideshow link written into kiosk frames.
    "public_url": ("PUBLIC_URL", ""),
    # Default display settings for frames that run the reframe slideshow.
    # Each frame can override them, and each screen can override them again
    # in its slideshow URL.
    "display_interval": ("DISPLAY_INTERVAL", "15"),
    "display_layout": ("DISPLAY_LAYOUT", "pair"),
    "display_orientation": ("DISPLAY_ORIENTATION", "auto"),
    "display_shuffle": ("DISPLAY_SHUFFLE", "yes"),
    "display_fill": ("DISPLAY_FILL", "blur"),
    # Screen settings for kiosk frames, applied by the agent on the frame.
    "screen_schedule": ("SCREEN_SCHEDULE", "always"),
    "screen_dark": ("SCREEN_DARK", "yes"),
    "screen_dark_lux": ("SCREEN_DARK_LUX", "5"),
    "screen_dark_seconds": ("SCREEN_DARK_SECONDS", "30"),
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


SCREEN_KEYS = ("screen_schedule", "screen_dark", "screen_dark_lux", "screen_dark_seconds")

SCREEN_CHOICES = {
    "screen_dark": {"yes": "Turn the screen off when the room is dark", "no": "Ignore the room light"},
}

SCREEN_RANGES = {"screen_dark_lux": (0, 1000), "screen_dark_seconds": (5, 3600)}

_HHMM = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")


def parse_schedule(value: str) -> Optional[tuple[str, str]]:
    """'23:00-07:00' -> ('23:00', '07:00'): the screen is off from the first to the second.
    None for 'always' (always on) and anything that isn't a schedule."""
    off, _, on = value.partition("-")
    if _HHMM.fullmatch(off) and _HHMM.fullmatch(on) and off != on:
        return off, on
    return None


def check_screen(key: str, value: str) -> Optional[str]:
    """Why a screen setting is invalid, or None ("" means the default)."""
    if not value:
        return None
    if key == "screen_schedule":
        if value != "always" and not parse_schedule(value):
            return "schedule: give 'always' or the hours the screen is off, like 23:00-07:00."
    elif key in SCREEN_CHOICES:
        if value not in SCREEN_CHOICES[key]:
            return f"{key}: choose one of {', '.join(SCREEN_CHOICES[key])}."
    else:
        low, high = SCREEN_RANGES[key]
        if not value.isdigit() or not low <= int(value) <= high:
            return f"{key}: give a whole number from {low} to {high}."
    return None


def screen_values(raw: dict[str, str]) -> dict[str, object]:
    """Validate screen_* strings into what the frame's agent uses."""
    schedule = parse_schedule(raw.get("screen_schedule", ""))
    values: dict[str, object] = {
        "schedule": {"off": schedule[0], "on": schedule[1]} if schedule else None,
        "dark": raw.get("screen_dark") != "no",
    }
    for key, (low, high) in SCREEN_RANGES.items():
        default = int(SETTINGS[key][1])
        try:
            value = max(low, min(high, int(raw.get(key, ""))))
        except ValueError:
            value = default
        values[key[len("screen_"):]] = value
    return values


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
    screen_schedule: str
    screen_dark: str
    screen_dark_lux: str
    screen_dark_seconds: str

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
    def screen_raw(self) -> dict[str, str]:
        return {key: getattr(self, key) for key in SCREEN_KEYS}

    @property
    def screen(self) -> dict[str, object]:
        """Default screen settings for kiosk frames, validated."""
        return screen_values(self.screen_raw)

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
