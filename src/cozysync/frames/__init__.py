"""Frame adapters: where photos go."""

from ..config import Config
from .base import Frame, FrameError
from .cozyla_adb import CozylaAdbFrame
from .folder import FolderFrame
from .web import WebFrame

KINDS = {
    "cozyla_adb": "Cozyla frame over adb (Wi-Fi)",
    "folder": "Folder or mounted share",
    "web": "Browser slideshow (any kiosk or web view)",
}


def get_frame(cfg: Config) -> Frame:
    if cfg.frame == "cozyla_adb":
        return CozylaAdbFrame(cfg.frame_address)
    if cfg.frame == "folder":
        return FolderFrame(cfg.frame_folder)
    if cfg.frame == "web":
        return WebFrame(cfg.data_dir)
    raise FrameError(f"unknown frame type {cfg.frame!r}; choose one of {', '.join(KINDS)}")


__all__ = ["Frame", "FrameError", "KINDS", "get_frame"]
