"""Frame adapters (where photos go) and the list of frames this worker drives."""

from ..config import Config
from .base import Frame, FrameError
from .cozyla_adb import CozylaAdbFrame
from .folder import FolderFrame
from .registry import KINDS, FrameConfig, display_for, load_all, web_media_dir
from .web import WebFrame

DEFAULT_MAX_PX = 1920


def build(cfg: Config, frame: FrameConfig) -> Frame:
    """The adapter for a configured frame. Cheap: nothing connects until it's used."""
    if frame.is_kiosk:
        # The kiosk plays the slideshow this server hosts.
        return WebFrame(web_media_dir(cfg.data_dir, frame.id))
    if frame.kind == "cozyla_adb":
        return CozylaAdbFrame(frame.address)
    if frame.kind == "folder":
        return FolderFrame(frame.folder)
    if frame.kind == "web":
        return WebFrame(web_media_dir(cfg.data_dir, frame.id))
    raise FrameError(f"unknown frame type {frame.kind!r}; choose one of {', '.join(KINDS)}")


def largest_max_px(cfg: Config) -> int:
    """Download size that suits every frame, for the listing test's samples."""
    return max((build(cfg, f).max_px for f in load_all(cfg)), default=DEFAULT_MAX_PX)


__all__ = ["Frame", "FrameConfig", "FrameError", "KINDS", "build", "display_for", "largest_max_px", "load_all"]
