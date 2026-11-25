"""What every photo source provides to the sync engine."""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class Album:
    id: str
    name: str


@dataclass(frozen=True)
class MediaItem:
    id: str  # stable within its source; the sync state is keyed on it
    mime_type: str
    created: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    # Source-specific details the source needs to fetch the item later.
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def is_video(self) -> bool:
        return self.mime_type.startswith("video/")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "MediaItem":
        return cls(**data)


@dataclass
class Listing:
    album: Album
    items: list[MediaItem]
    # False when the source couldn't confirm it saw every item (page cap hit,
    # partial error). Removals must never be inferred from an incomplete listing.
    complete: bool


class Source(Protocol):
    kind: str

    def albums(self) -> list[Album]:
        """Albums the user chose to show."""
        ...

    def list_album(self, album: Album) -> Listing:
        """Every item in the album, in one listing."""
        ...

    def fetch(self, item: MediaItem, dest: Path, max_px: int) -> None:
        """Download the item to `dest`, fitted within max_px x max_px for photos."""
        ...
