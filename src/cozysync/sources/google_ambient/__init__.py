"""Google Photos through the Ambient API.

Needs a Google Cloud project accepted into the Google Photos partner program;
personal projects get 403 on device registration (docs/decisions.md, entry 2).
Sign-in and device registration live in `login`; this module is the read side.
"""

from pathlib import Path
from typing import Any

from ...config import Config
from ...store import read_json
from ..base import Album, Listing, MediaItem
from .api import AmbientClient, AmbientError
from .auth import AuthError, TokenStore

# Each page is one of the 240 daily mediaItems.list calls per device.
DEFAULT_MAX_PAGES = 20


def to_media_item(raw: dict[str, Any]) -> MediaItem:
    mf = raw.get("mediaFile", {})
    meta = mf.get("mediaFileMetadata", {})
    return MediaItem(
        id=raw["id"],
        mime_type=mf.get("mimeType", ""),
        created=raw.get("createTime"),
        width=meta.get("width"),
        height=meta.get("height"),
        extra={"base_url": mf.get("baseUrl")},
    )


class AmbientSource:
    kind = "google_ambient"

    def __init__(self, cfg: Config, max_pages: int = DEFAULT_MAX_PAGES):
        self.cfg = cfg
        self.max_pages = max_pages
        self.client = AmbientClient(TokenStore(cfg))

    def _device_id(self) -> str:
        saved = read_json(self.cfg.device_file)
        if saved is None:
            raise AuthError("no Ambient device yet; run `cozysync login` first")
        return saved["id"]

    def albums(self) -> list[Album]:
        device = self.client.get_device(self._device_id())
        return [Album(s["id"], s.get("displayName") or s["id"]) for s in device.get("mediaSources", [])]

    def list_album(self, album: Album) -> Listing:
        # Always per album (mediaSourceId): the default listing is a curated
        # mix and can't be used to detect removals.
        raw, complete = self.client.list_all_media_items(self._device_id(), album.id, max_pages=self.max_pages)
        return Listing(album=album, items=[to_media_item(r) for r in raw], complete=complete)

    def fetch(self, item: MediaItem, dest: Path, max_px: int) -> None:
        # baseUrls expire, so fetch soon after listing.
        suffix = "=dv" if item.is_video else f"=w{max_px}-h{max_px}"
        resp = self.client.download(item.extra["base_url"], suffix)
        if not resp.ok:
            raise AmbientError(f"download {item.id} -> HTTP {resp.status_code}")
        dest.write_bytes(resp.content)


__all__ = ["AmbientSource", "AmbientError", "AuthError"]
