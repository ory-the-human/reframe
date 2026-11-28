"""The frames this worker drives, saved in /data/frames.json.

Each frame has its own type and target, the albums it shows, and optional
display overrides on top of the defaults in settings.
"""

import hmac
import os
import secrets
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import FRAME_SETTING, Config, typed
from ..store import read_json, write_json
from .base import FrameError

KINDS = {
    "cozyla_adb": "Cozyla frame over adb (Wi-Fi)",
    "folder": "Folder or mounted share",
    "web": "Browser slideshow (any kiosk or web view)",
}


@dataclass
class FrameConfig:
    id: str
    name: str
    kind: str
    address: str = ""  # cozyla_adb: host:port
    folder: str = ""  # folder: path inside the container
    key: str = ""  # web: the secret in the slideshow URL
    # [{"id", "name"}]; empty means every album the source offers. Names are
    # kept for display so the dashboard needn't call the source.
    albums: list[dict[str, str]] = field(default_factory=list)
    # Overrides of the frame setting defaults (config.FRAME_SETTINGS), by key.
    display: dict[str, str] = field(default_factory=dict)
    # cozyla_adb provisioned as a kiosk (frames/kiosk.py): the kiosk settings
    # password, the original state to restore, etc. Empty when not provisioned.
    kiosk: dict[str, Any] = field(default_factory=dict)
    # The secret the agent app on a kiosk frame uses to call this server (agent.py).
    agent_key: str = ""

    @property
    def is_kiosk(self) -> bool:
        return bool(self.kiosk.get("provisioned_at"))

    @property
    def shows_slideshow(self) -> bool:
        return self.kind == "web" or self.is_kiosk

    @property
    def target(self) -> str:
        if self.kind == "cozyla_adb":
            return self.address
        if self.kind == "folder":
            return self.folder
        return "browser slideshow"

    @property
    def album_ids(self) -> set[str]:
        return {a["id"] for a in self.albums}

    def shows(self, album_id: str) -> bool:
        return not self.albums or album_id in self.album_ids


def frames_file(data_dir: Path) -> Path:
    return data_dir / "frames.json"


def web_media_dir(data_dir: Path, frame_id: str) -> Path:
    return data_dir / "frames" / frame_id


def _new_id() -> str:
    return "f-" + secrets.token_hex(3)


def _migrate(cfg: Config) -> list[FrameConfig]:
    """One-time: turn the old frame settings (FRAME, FRAME_ADDRESS, …) into the first frames.

    FRAME_ADDRESS may list several adb addresses, comma-separated: one frame each.
    """
    saved = read_json(cfg.data_dir / "settings.json") or {}

    def legacy(key: str, env: str) -> str:
        return saved.get(key) or os.environ.get(env, "")

    kind, address, folder = legacy("frame", "FRAME"), legacy("frame_address", "FRAME_ADDRESS"), legacy("frame_folder", "FRAME_FOLDER")
    if not (kind or address or folder):
        return []
    kind = kind if kind in KINDS else "cozyla_adb"
    addresses = [a.strip() for a in address.split(",") if a.strip()]
    if kind == "cozyla_adb" and len(addresses) > 1:
        return [FrameConfig(id=_new_id(), name=f"Frame {i}", kind=kind, address=a) for i, a in enumerate(addresses, 1)]
    frame = FrameConfig(id=_new_id(), name="Frame 1", kind=kind, address=address, folder=folder or "/frame")
    if kind == "web":
        # Keep the old slideshow link and photos working.
        old_key = cfg.data_dir / "web_frame_key"
        frame.key = old_key.read_text().strip() if old_key.exists() else secrets.token_urlsafe(18)
        old_dir = cfg.data_dir / "web-frame"
        if old_dir.is_dir():
            web_media_dir(cfg.data_dir, frame.id).parent.mkdir(parents=True, exist_ok=True)
            old_dir.rename(web_media_dir(cfg.data_dir, frame.id))
        old_key.unlink(missing_ok=True)
    return [frame]


def load_all(cfg: Config) -> list[FrameConfig]:
    data = read_json(frames_file(cfg.data_dir))
    if data is None:
        frames = _migrate(cfg)
        save_all(cfg, frames)
        return frames
    return [FrameConfig(**f) for f in data["frames"]]


def save_all(cfg: Config, frames: list[FrameConfig]) -> None:
    write_json(frames_file(cfg.data_dir), {"frames": [asdict(f) for f in frames]})


def find(frames: list[FrameConfig], ref: str) -> FrameConfig:
    """By id, or by name ignoring case."""
    for f in frames:
        if f.id == ref or f.name.lower() == ref.lower():
            return f
    raise FrameError(f"no frame {ref!r}; frames: {', '.join(f.name for f in frames) or 'none'}")


def by_key(frames: list[FrameConfig], key: str) -> Optional[FrameConfig]:
    for f in frames:
        if f.shows_slideshow and f.key and hmac.compare_digest(f.key, key):
            return f
    return None


def by_agent_key(frames: list[FrameConfig], key: str) -> Optional[FrameConfig]:
    for f in frames:
        if f.agent_key and hmac.compare_digest(f.agent_key, key):
            return f
    return None


def add(cfg: Config, name: str, kind: str, **fields: Any) -> FrameConfig:
    if kind not in KINDS:
        raise FrameError(f"unknown frame type {kind!r}; choose one of {', '.join(KINDS)}")
    frames = load_all(cfg)
    if any(f.name.lower() == name.lower() for f in frames):
        raise FrameError(f"a frame named {name!r} already exists")
    frame = FrameConfig(id=_new_id(), name=name, kind=kind, **fields)
    if kind == "web":
        frame.key = secrets.token_urlsafe(18)
    validate(frame)
    save_all(cfg, frames + [frame])
    return frame


def update(cfg: Config, frame: FrameConfig) -> None:
    frames = load_all(cfg)
    if any(f.name.lower() == frame.name.lower() and f.id != frame.id for f in frames):
        raise FrameError(f"a frame named {frame.name!r} already exists")
    validate(frame)
    save_all(cfg, [frame if f.id == frame.id else f for f in frames])


def remove(cfg: Config, frame_id: str) -> None:
    frames = load_all(cfg)
    if any(f.id == frame_id and f.kiosk for f in frames):
        raise FrameError("restore this frame first: removing it would lose the record of how to undo the kiosk setup")
    save_all(cfg, [f for f in frames if f.id != frame_id])
    # A web frame's photos are only copies for the slideshow.
    shutil.rmtree(web_media_dir(cfg.data_dir, frame_id), ignore_errors=True)
    (cfg.data_dir / "agents" / f"{frame_id}.json").unlink(missing_ok=True)


def validate(frame: FrameConfig) -> None:
    if not frame.name.strip():
        raise FrameError("a frame needs a name")
    if frame.kind == "cozyla_adb" and not frame.address:
        raise FrameError("a Cozyla frame needs its adb address, e.g. 192.168.1.50:5555")
    if frame.kind == "folder" and not frame.folder:
        raise FrameError("a folder frame needs a folder")


def settings(cfg: Config, frame: FrameConfig) -> dict[str, str]:
    """The frame's raw setting values: its overrides on top of the defaults."""
    return {**cfg.defaults, **{k: v for k, v in frame.display.items() if v}}


def display_for(cfg: Config, frame: FrameConfig) -> dict[str, object]:
    return typed(settings(cfg, frame), "display_")


def converts_videos(cfg: Config, frame: FrameConfig) -> bool:
    return FRAME_SETTING["convert_videos"].typed(settings(cfg, frame)["convert_videos"])


def screen_for(cfg: Config, frame: FrameConfig) -> dict[str, object]:
    return typed(settings(cfg, frame), "screen_")
