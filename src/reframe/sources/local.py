"""Albums that live on the server, in /data/albums/<album id>/.

Created and filled from the web UI; files copied into an album's folder by hand
show up too. A listing is a directory scan, so it's always complete.
"""

import hashlib
import mimetypes
import re
import secrets
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from ..config import Config
from ..store import read_json, write_json
from .base import Album, Listing, MediaItem

PREFIX = "local-"


class LocalAlbumError(RuntimeError):
    pass


def albums_file(cfg: Config) -> Path:
    return cfg.data_dir / "local_albums.json"


def dates_file(cfg: Config) -> Path:
    return cfg.data_dir / "local_dates.json"


def album_dir(cfg: Config, album_id: str) -> Path:
    return cfg.data_dir / "albums" / album_id


def saved_albums(cfg: Config) -> list[dict[str, str]]:
    """[{"id", "name"}]"""
    return (read_json(albums_file(cfg)) or {}).get("albums", [])


def _save(cfg: Config, albums: list[dict[str, str]]) -> None:
    write_json(albums_file(cfg), {"albums": albums})


def find(cfg: Config, ref: str) -> dict[str, str]:
    for a in saved_albums(cfg):
        if ref in (a["id"], a["name"]):
            return a
    raise LocalAlbumError(f"no album {ref!r}")


def add(cfg: Config, name: str) -> dict[str, str]:
    entry = {"id": PREFIX + secrets.token_hex(6), "name": name.strip() or "Local album"}
    album_dir(cfg, entry["id"]).mkdir(parents=True)
    _save(cfg, saved_albums(cfg) + [entry])
    return entry


def remove(cfg: Config, ref: str) -> dict[str, str]:
    a = find(cfg, ref)
    _save(cfg, [b for b in saved_albums(cfg) if b["id"] != a["id"]])
    shutil.rmtree(album_dir(cfg, a["id"]), ignore_errors=True)
    dates = read_json(dates_file(cfg)) or {}
    if dates.pop(a["id"], None) is not None:
        write_json(dates_file(cfg), dates)
    return a


def rename(cfg: Config, ref: str, name: str) -> None:
    albums = saved_albums(cfg)
    for a in albums:
        if ref in (a["id"], a["name"]):
            a["name"] = name.strip() or a["name"]
            _save(cfg, albums)
            return
    raise LocalAlbumError(f"no album {ref!r}")


def media_type(name: str) -> Optional[str]:
    mime = mimetypes.guess_type(name)[0] or ""
    return mime if mime.startswith(("image/", "video/")) else None


def files(cfg: Config, album_id: str) -> list[tuple[Path, str]]:
    """The album's photos and videos, with their MIME types."""
    folder = album_dir(cfg, album_id)
    found = [(p, media_type(p.name)) for p in sorted(folder.iterdir()) if p.is_file()] if folder.is_dir() else []
    return [(p, mime) for p, mime in found if mime]


def save_upload(cfg: Config, album_id: str, upload) -> str:
    """Saves a werkzeug FileStorage named after its content, so phones' repeated names (image.jpg) don't clash."""
    tmp = album_dir(cfg, album_id) / f".upload-{secrets.token_hex(4)}"
    upload.save(tmp)
    digest = hashlib.sha256()
    with open(tmp, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    name = digest.hexdigest()[:16] + Path(upload.filename or "").suffix.lower()
    tmp.replace(tmp.with_name(name))
    return name


def _photo_created(path: Path) -> Optional[str]:
    try:
        with Image.open(path) as img:
            exif = img.getexif().get_ifd(0x8769)
        taken, offset = exif.get(36867), exif.get(36881)  # DateTimeOriginal, OffsetTimeOriginal
        return datetime.strptime(taken + (offset or ""), "%Y:%m:%d %H:%M:%S" + ("%z" if offset else "")).isoformat() if taken else None
    except (OSError, ValueError):  # not a photo Pillow reads, or a blank date like 0000:00:00
        return None


def _video_created(path: Path) -> Optional[str]:
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    except FileNotFoundError:
        return None
    match = re.search(r"creation_time\s*:\s*(\S+)", out)
    return datetime.fromisoformat(match.group(1).replace("Z", "+00:00")).astimezone().isoformat() if match else None


class LocalSource:
    kind = "local"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def albums(self) -> list[Album]:
        return [Album(a["id"], a["name"]) for a in saved_albums(self.cfg)]

    def list_album(self, album: Album) -> Listing:
        cache = read_json(dates_file(self.cfg)) or {}
        dates, items = {}, []
        for path, mime in files(self.cfg, album.id):
            st = path.stat()
            # Changes when the file is replaced, so frames get the new one.
            item_id = hashlib.sha256(f"{album.id}/{path.name}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:32]
            dates[item_id] = cache.get(album.id, {}).get(item_id) or (
                (_video_created if mime.startswith("video/") else _photo_created)(path)
                or datetime.fromtimestamp(st.st_mtime).astimezone().isoformat()
            )
            items.append(MediaItem(id=item_id, mime_type=mime, created=dates[item_id], extra={"local": f"{album.id}/{path.name}"}))
        if dates != cache.get(album.id):
            write_json(dates_file(self.cfg), {**cache, album.id: dates})
        return Listing(album=album, items=items, complete=True, trusted=True)

    def fetch(self, item: MediaItem, dest: Path, max_px: int) -> None:
        src = self.cfg.data_dir / "albums" / item.extra["local"]
        if item.is_video:
            shutil.copyfile(src, dest)
            return
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_px, max_px))
            img.convert("RGB").save(dest, "JPEG", quality=90)
