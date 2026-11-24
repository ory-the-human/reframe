"""Photo sources: where photos come from."""

from ..config import Config
from .base import Album, Listing, MediaItem, Source

KINDS = {
    "google_ambient": "Google Photos (Ambient API, needs partner approval)",
}


def get_source(cfg: Config) -> Source:
    if cfg.source == "google_ambient":
        from .google_ambient import AmbientSource

        return AmbientSource(cfg)
    raise ValueError(f"unknown source {cfg.source!r}; choose one of {', '.join(KINDS)}")


__all__ = ["Album", "KINDS", "Listing", "MediaItem", "Source", "get_source"]
