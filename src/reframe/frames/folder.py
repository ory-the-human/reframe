"""A plain directory: a mounted network share, a USB drive, or a folder a
TV, DLNA server or slideshow app reads from."""

import shutil
from pathlib import Path

from .base import FrameError


class FolderFrame:
    kind = "folder"

    def __init__(self, directory: str, max_px: int = 1920):
        self.directory = Path(directory)
        self.max_px = max_px

    def check(self) -> dict[str, str]:
        if not self.directory.is_dir():
            raise FrameError(f"{self.directory} is not a directory (is the share mounted?)")
        usage = shutil.disk_usage(self.directory)
        return {"directory": str(self.directory), "free": f"{usage.free / 2**30:.1f} GB"}

    def put(self, local: Path, name: str) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / name
        # Copied under another name first, so a slideshow never sees half a file.
        part = self.directory / f".{name}.part"
        shutil.copyfile(local, part)
        part.replace(target)
        return str(target)

    def remove(self, frame_path: str) -> None:
        path = Path(frame_path)
        if path.parent.resolve() != self.directory.resolve():
            raise FrameError(f"refusing to delete {frame_path}: outside {self.directory}")
        path.unlink(missing_ok=True)
