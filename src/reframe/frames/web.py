"""A browser slideshow served by the worker itself.

Any kiosk browser or web view opens the frame's secret slideshow URL (shown on
the dashboard) and plays its photos full screen. Photos are kept in
`<data>/frames/<frame id>/`, since the worker is the frame.
"""

from pathlib import Path

from .folder import FolderFrame

MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm"}
VIDEO_SUFFIXES = {".mp4", ".webm"}


def list_media(directory: Path) -> list[dict[str, str]]:
    if not directory.is_dir():
        return []
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES)
    return [{"name": p.name, "type": "video" if p.suffix.lower() in VIDEO_SUFFIXES else "image"} for p in files]


class WebFrame(FolderFrame):
    kind = "web"

    def __init__(self, directory: Path, max_px: int = 1920):
        super().__init__(str(directory), max_px=max_px)
        self.directory.mkdir(parents=True, exist_ok=True)

    def check(self) -> dict[str, str]:
        info = super().check()
        info["photos"] = str(len(list_media(self.directory)))
        return info
