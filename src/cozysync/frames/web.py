"""A browser slideshow served by the worker itself.

Any kiosk browser or web view opens the secret slideshow URL (shown on the
dashboard) and plays the photos full screen. Photos are kept in
`<data>/web-frame/`, since the worker is the frame.
"""

import os
import secrets
from pathlib import Path

from .folder import FolderFrame

MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm"}
VIDEO_SUFFIXES = {".mp4", ".webm"}


def media_dir(data_dir: Path) -> Path:
    return data_dir / "web-frame"


def slideshow_key(data_dir: Path) -> str:
    """The secret in the slideshow URL; kiosks can't do the UI's password prompt."""
    path = data_dir / "web_frame_key"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(18))
    return path.read_text().strip()


def list_media(directory: Path) -> list[dict[str, str]]:
    if not directory.is_dir():
        return []
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES)
    return [{"name": p.name, "type": "video" if p.suffix.lower() in VIDEO_SUFFIXES else "image"} for p in files]


class WebFrame(FolderFrame):
    kind = "web"

    def __init__(self, data_dir: Path, max_px: int = 1920):
        super().__init__(str(media_dir(data_dir)), max_px=max_px)
        self.directory.mkdir(parents=True, exist_ok=True)

    def check(self) -> dict[str, str]:
        info = super().check()
        info["photos"] = str(len(list_media(self.directory)))
        info["slideshow"] = "open the slideshow link on the dashboard in any browser"
        return info
