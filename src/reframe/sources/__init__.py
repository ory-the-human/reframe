"""Photo sources: where photos come from."""

from pathlib import Path

from ..config import Config
from .base import Album, Listing, MediaItem, Source
from .local import PREFIX, LocalSource

KINDS = {
    "share_link": "Google Photos shared albums (share links)",
    "google_ambient": "Google Photos (Ambient API, needs partner approval)",
}


class CombinedSource:
    """The configured source plus the server's local albums."""

    def __init__(self, main: Source, local: Source):
        self.main, self.local = main, local
        self.kind = main.kind

    def albums(self) -> list[Album]:
        return self.main.albums() + self.local.albums()

    def list_album(self, album: Album) -> Listing:
        return (self.local if album.id.startswith(PREFIX) else self.main).list_album(album)

    def fetch(self, item: MediaItem, dest: Path, max_px: int) -> None:
        (self.local if "local" in item.extra else self.main).fetch(item, dest, max_px)


def _main_source(cfg: Config) -> Source:
    if cfg.source == "share_link":
        from .share_link import ShareLinkSource

        return ShareLinkSource(cfg)
    if cfg.source == "google_ambient":
        from .google_ambient import AmbientSource

        return AmbientSource(cfg)
    raise ValueError(f"unknown source {cfg.source!r}; choose one of {', '.join(KINDS)}")


def get_source(cfg: Config) -> Source:
    return CombinedSource(_main_source(cfg), LocalSource(cfg))


__all__ = ["Album", "CombinedSource", "KINDS", "Listing", "MediaItem", "Source", "get_source"]
