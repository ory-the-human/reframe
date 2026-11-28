import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from .store import read_json, write_json

_HHMM = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_YES_NO = {"yes": "On", "no": "Off"}


def parse_schedule(value: str) -> Optional[tuple[str, str]]:
    """'23:00-07:00' -> ('23:00', '07:00'): the screen is off from the first to the second.
    None for 'always' (always on) and anything that isn't a schedule."""
    off, _, on = value.partition("-")
    if _HHMM.fullmatch(off) and _HHMM.fullmatch(on) and off != on:
        return off, on
    return None


@dataclass(frozen=True)
class Setting:
    """A frame setting: a default in settings.json (or env KEY), overridable per frame in its `display`."""

    key: str
    label: str
    kind: str  # choice | switch | number | schedule
    default: str
    group: str  # Slideshow | Videos | Screen
    applies: str = "all"  # all | slideshow | kiosk
    options: dict[str, str] = field(default_factory=dict)
    range: tuple[int, int] = (0, 0)
    unit: str = ""
    hint: str = ""

    @property
    def name(self) -> str:
        """The key in display_for/screen_for, the CLI flag and the slideshow URL parameter."""
        return self.key.removeprefix("display_").removeprefix("screen_")

    @property
    def choices(self) -> dict[str, str]:
        return _YES_NO if self.kind == "switch" else self.options

    def check(self, value: str) -> Optional[str]:
        """Why a value is invalid, or None ("" means the default)."""
        if not value:
            return None
        if self.kind == "schedule":
            if value != "always" and not parse_schedule(value):
                return f"{self.name}: give 'always' or the hours the screen is off, like 23:00-07:00."
        elif self.kind == "number":
            low, high = self.range
            if not value.isdigit() or not low <= int(value) <= high:
                return f"{self.name}: give a whole number from {low} to {high}."
        elif value not in self.choices:
            return f"{self.name}: choose one of {', '.join(self.choices)}."
        return None

    def typed(self, value: str) -> Any:
        """What the slideshow and the agent use; invalid values fall back to the default."""
        if self.kind == "schedule":
            schedule = parse_schedule(value)
            return {"off": schedule[0], "on": schedule[1]} if schedule else None
        if self.kind == "number":
            low, high = self.range
            try:
                return max(low, min(high, int(value)))
            except ValueError:
                return int(self.default)
        value = value if value in self.choices else self.default
        return value == "yes" if self.kind == "switch" else value

    def from_form(self, form: Mapping) -> Optional[str]:
        """The value a submitted form sets, or None if the form doesn't carry this setting."""
        values = form.getlist(self.key)
        if not values:
            return None
        if self.kind == "schedule":
            return f"{form.get('screen_off_at', '')}-{form.get('screen_on_at', '')}" if values[-1] == "yes" else "always"
        # A switch posts a hidden "no" before its checkbox's "yes".
        return values[-1].strip()


FRAME_SETTINGS = (
    Setting("display_interval", "Show each photo for", "number", "15", "Slideshow", "slideshow", range=(3, 3600), unit="seconds"),
    Setting("display_layout", "Layout", "choice", "pair", "Slideshow", "slideshow",
            {"pair": "Two photos together when they fit better", "single": "One photo at a time"}),
    Setting("display_orientation", "Orientation", "choice", "auto", "Slideshow", "slideshow",
            {"auto": "Follow the screen", "landscape": "Landscape", "portrait": "Portrait",
             "landscape-flipped": "Landscape, upside down", "portrait-flipped": "Portrait, upside down"},
            hint="Pick one when a screen is mounted sideways but its browser doesn't rotate"),
    Setting("display_shuffle", "Shuffle", "switch", "yes", "Slideshow", "slideshow"),
    Setting("display_fill", "Space around photos", "choice", "blur", "Slideshow", "slideshow",
            {"blur": "A blurred copy of the photo", "black": "Black"}),
    Setting("display_date", "Show the date taken", "switch", "yes", "Slideshow", "slideshow"),
    Setting("display_sound", "Video sound", "switch", "no", "Slideshow", "kiosk"),
    Setting("display_volume", "Volume", "number", "50", "Slideshow", "kiosk", range=(0, 100), unit="%",
            hint="Also set on the frame: tap the slideshow, then the gear"),
    Setting("convert_videos", "Convert videos for the screen", "switch", "yes", "Videos",
            hint="Re-encodes new videos to H.264 sized for the frame, so Cozyla frames play them cleanly"),
    Setting("screen_schedule", "Turn off at night", "schedule", "always", "Screen", "kiosk"),
    Setting("screen_dark", "Turn off when the room is dark", "switch", "yes", "Screen", "kiosk",
            hint="It comes back on when the room is lit; a touch wakes it for 5 minutes"),
    Setting("screen_dark_lux", "Dark is below", "number", "5", "Screen", "kiosk", range=(0, 1000), unit="lux"),
    Setting("screen_dark_seconds", "Dark for at least", "number", "30", "Screen", "kiosk", range=(5, 3600), unit="seconds"),
)
FRAME_SETTING = {s.key: s for s in FRAME_SETTINGS}

# Settings editable from the web UI. Values saved there live in
# /data/settings.json and take precedence over the environment, which only
# provides defaults. Frames have their own file (frames/registry.py).
SETTINGS = {
    "source": ("SOURCE", "share_link"),
    "client_id": ("GOOGLE_CLIENT_ID", ""),
    "client_secret": ("GOOGLE_CLIENT_SECRET", ""),
    "device_name": ("DEVICE_NAME", "reframe"),
    # How frames reach this server, e.g. http://192.168.1.10:1992, for the
    # slideshow link written into kiosk frames.
    "public_url": ("PUBLIC_URL", ""),
    # Minutes between syncs; 0 turns the automatic sync off.
    "sync_minutes": ("SYNC_MINUTES", "60"),
    **{s.key: (s.key.upper(), s.default) for s in FRAME_SETTINGS},
}

SYNC_CHOICES = {"0": "Never", "15": "Every 15 minutes", "30": "Every 30 minutes", "60": "Every hour",
                "360": "Every 6 hours", "1440": "Once a day"}


def settings_for(frame: Any) -> list[Setting]:
    """The settings that apply to a frame (FrameConfig)."""
    applies = {"all": True, "slideshow": frame.shows_slideshow, "kiosk": frame.is_kiosk}
    return [s for s in FRAME_SETTINGS if applies[s.applies]]


def typed(raw: Mapping[str, str], prefix: str) -> dict[str, Any]:
    """The typed values of the settings whose key starts with `prefix`, by name."""
    return {s.name: s.typed(raw.get(s.key, "")) for s in FRAME_SETTINGS if s.key.startswith(prefix)}


@dataclass(frozen=True)
class Config:
    source: str
    client_id: str
    client_secret: str
    data_dir: Path
    device_name: str
    public_url: str
    sync_minutes: str
    defaults: dict[str, str]  # frame settings, by key

    def get(self, key: str) -> str:
        return self.defaults[key] if key in FRAME_SETTING else getattr(self, key)

    @property
    def token_file(self) -> Path:
        return self.data_dir / "token.json"

    @property
    def device_file(self) -> Path:
        return self.data_dir / "device.json"

    @property
    def sync_interval(self) -> int:
        """Seconds between automatic syncs; 0 for off."""
        try:
            return max(0, int(self.sync_minutes)) * 60
        except ValueError:
            return 3600

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
    defaults = {key: values.pop(key) for key in FRAME_SETTING}
    return Config(data_dir=data_dir(), defaults=defaults, **values)


def save_settings(updates: dict[str, Any]) -> None:
    saved = read_json(settings_file()) or {}
    saved.update({k: v for k, v in updates.items() if k in SETTINGS})
    write_json(settings_file(), saved)
