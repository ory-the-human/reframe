"""Google Photos shared albums, read through their share links.

There's no API for these: this reads what the album's web page reads, Google's
internal `snAcKc` call, one page at a time. Google can change it without notice,
so anything unexpected makes the listing incomplete (nothing is removed) or
fails it, never a guess.

The links are secrets (anyone with one sees the album), so they're kept in
/data/albums.json, readable only by the worker. Making the album private again,
or turning link sharing off, revokes the link.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import requests

from ..config import Config
from ..store import read_json, write_json
from .base import Album, Listing, MediaItem

RPC = "snAcKc"
BATCH_URL = "https://photos.google.com/_/PhotosUi/data/batchexecute"
MAX_PAGES = 200
HEADERS = {"User-Agent": "Mozilla/5.0 (reframe)", "Accept-Language": "en"}
TIMEOUT = 30


class ShareLinkError(RuntimeError):
    pass


def albums_file(cfg: Config):
    return cfg.data_dir / "albums.json"


def saved_albums(cfg: Config) -> list[dict[str, str]]:
    """[{"id", "name", "url", "key"}]"""
    return (read_json(albums_file(cfg)) or {}).get("albums", [])


def _save(cfg: Config, albums: list[dict[str, str]]) -> None:
    write_json(albums_file(cfg), {"albums": albums})


def resolve(url: str) -> tuple[str, str]:
    """A share link (photos.app.goo.gl/… or photos.google.com/share/…) -> (album id, key)."""
    url = url.strip()
    if not re.match(r"^https://(photos\.app\.goo\.gl|photos\.google\.com|goo\.gl)/", url):
        raise ShareLinkError("that isn't a Google Photos share link (https://photos.app.goo.gl/…)")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    if resp.status_code == 404:
        raise ShareLinkError("Google Photos says that album doesn't exist (or isn't shared any more)")
    resp.raise_for_status()
    final = urlparse(resp.url)
    match = re.match(r"^/share/([A-Za-z0-9_-]+)", final.path)
    key = parse_qs(final.query).get("key", [None])[0]
    if not match or not key:
        raise ShareLinkError("that link doesn't open a shared album")
    return match.group(1), key


def add(cfg: Config, url: str, name: str = "") -> dict[str, str]:
    album_id, key = resolve(url)
    albums = saved_albums(cfg)
    if any(a["id"] == album_id for a in albums):
        raise ShareLinkError("that album is already added")
    page = _page(album_id, key, None)
    title = name.strip() or _title(page) or "Shared album"
    entry = {"id": album_id, "name": title, "url": url.strip(), "key": key}
    _save(cfg, albums + [entry])
    return entry


def remove(cfg: Config, ref: str) -> dict[str, str]:
    albums = saved_albums(cfg)
    for a in albums:
        if ref in (a["id"], a["name"]):
            _save(cfg, [b for b in albums if b is not a])
            return a
    raise ShareLinkError(f"no album {ref!r}")


def rename(cfg: Config, ref: str, name: str) -> None:
    albums = saved_albums(cfg)
    for a in albums:
        if ref in (a["id"], a["name"]):
            a["name"] = name.strip() or a["name"]
            _save(cfg, albums)
            return
    raise ShareLinkError(f"no album {ref!r}")


def _page(album_id: str, key: str, token: Optional[str]) -> list[Any]:
    """One page of the album: [null, items, next token ("" on the last), album details, …]."""
    req = json.dumps([[[RPC, json.dumps([album_id, token, None, key]), None, "generic"]]])
    resp = requests.post(
        BATCH_URL,
        data={"f.req": req},
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise ShareLinkError(f"Google Photos answered HTTP {resp.status_code}")
    # The reply starts with )]}' and holds lines of JSON; the one tagged
    # ["wrb.fr", "snAcKc", "<payload JSON>"] is ours.
    for line in resp.text.splitlines():
        line = line.strip()
        if not line.startswith("[["):
            continue
        try:
            envelope = json.loads(line)
        except ValueError:
            continue
        for entry in envelope:
            if isinstance(entry, list) and len(entry) > 2 and entry[0] == "wrb.fr" and entry[1] == RPC:
                if not entry[2]:
                    raise ShareLinkError("Google Photos returned no album (the link may have been turned off)")
                page = json.loads(entry[2])
                if not isinstance(page, list) or len(page) < 2:
                    raise ShareLinkError("Google Photos' album format has changed")
                return page
    raise ShareLinkError("Google Photos' reply has changed: no album in it")


def _title(page: list[Any]) -> Optional[str]:
    details = page[3] if len(page) > 3 and isinstance(page[3], list) else []
    return details[1] if len(details) > 1 and isinstance(details[1], str) else None


def to_media_item(raw: Any) -> Optional[MediaItem]:
    """[id, [url, width, height, …], taken (ms), …, zone offset (ms), …], with video details further on."""
    if not (isinstance(raw, list) and len(raw) > 2 and isinstance(raw[0], str) and isinstance(raw[1], list)):
        return None
    image = raw[1]
    if not (len(image) > 2 and isinstance(image[0], str) and image[0].startswith("https://")
            and isinstance(image[1], int) and isinstance(image[2], int)):
        return None
    taken = raw[2] if isinstance(raw[2], (int, float)) else None
    # The offset (ms) of where it was taken, so the date is the local one.
    zone = timezone(timedelta(milliseconds=raw[4])) if len(raw) > 4 and isinstance(raw[4], int) else timezone.utc
    return MediaItem(
        id=raw[0],
        mime_type="video/mp4" if _is_video(raw) else "image/jpeg",
        created=datetime.fromtimestamp(taken // 1000, zone).isoformat() if taken else None,
        width=image[1],
        height=image[2],
        extra={"url": image[0]},
    )


def _is_video(raw: list[Any]) -> bool:
    # Videos carry a details object keyed "76647426" (duration, status).
    # Live Photos carry "146008172" (motion clip) instead and stay photos.
    return any(isinstance(x, dict) and "76647426" in x for x in raw)


class ShareLinkSource:
    kind = "share_link"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def albums(self) -> list[Album]:
        return [Album(a["id"], a["name"]) for a in saved_albums(self.cfg)]

    def _key(self, album: Album) -> str:
        for a in saved_albums(self.cfg):
            if a["id"] == album.id:
                return a["key"]
        raise ShareLinkError(f"album {album.name} isn't added any more")

    def list_album(self, album: Album) -> Listing:
        key = self._key(album)
        items: list[MediaItem] = []
        seen: set[str] = set()
        token: Optional[str] = None
        complete = False
        for _ in range(MAX_PAGES):
            page = _page(album.id, key, token)
            raws = page[1] or []
            for raw in raws:
                item = to_media_item(raw)
                if item is None:
                    # Something we don't understand: don't trust this listing for removals.
                    return Listing(album=album, items=items, complete=False)
                if item.id not in seen:
                    seen.add(item.id)
                    items.append(item)
            token = page[2] if len(page) > 2 and isinstance(page[2], str) and page[2] else None
            if token is None:
                # The album details hold its item count: a mismatch means we missed some.
                details = page[3] if len(page) > 3 and isinstance(page[3], list) else []
                complete = len(details) > 21 and details[21] == len(items)
                break
        return Listing(album=album, items=items, complete=complete)

    def fetch(self, item: MediaItem, dest: Path, max_px: int) -> None:
        # =m22 is Google's 720p H.264 copy (frames can't play HEVC originals, =dv);
        # =w…-h… a photo fitted within the box.
        suffix = "=m22" if item.is_video else f"=w{max_px}-h{max_px}"
        resp = requests.get(item.extra["url"] + suffix, headers=HEADERS, timeout=120, stream=True)
        if resp.status_code != 200:
            raise ShareLinkError(f"download {item.id} -> HTTP {resp.status_code}")
        with open(dest, "wb") as out:
            for chunk in resp.iter_content(1 << 16):
                out.write(chunk)
