"""What every frame adapter provides to the sync engine."""

from pathlib import Path
from typing import Protocol


class FrameError(RuntimeError):
    pass


class Frame(Protocol):
    kind: str
    # Longest edge, in pixels, worth downloading for this display.
    max_px: int

    def check(self) -> dict[str, str]:
        """Connect and describe the frame; raise FrameError if unreachable."""
        ...

    def put(self, local: Path, name: str) -> str:
        """Copy a local file onto the frame and return where it lives there."""
        ...

    def remove(self, frame_path: str) -> None:
        """Delete a file previously returned by put(). Missing files are fine."""
        ...
