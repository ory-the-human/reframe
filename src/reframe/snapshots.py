"""Full album listings saved to disk, and diffs between them.

Used to check that a source's listings are complete and stable before trusting
them for removal detection.
"""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import Config
from .sources import Album, Listing, MediaItem, Source
from .store import read_json, write_json


def take(cfg: Config, source: Source, albums: list[Album]) -> tuple[Path, dict[str, Any], list[Listing]]:
    """List each album in full and save the result."""
    listings = [source.list_album(album) for album in albums]
    snapshot: dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "source": source.kind,
        "albums": {
            listing.album.id: {
                "name": listing.album.name,
                "complete": listing.complete,
                "items": [item.to_json() for item in listing.items],
            }
            for listing in listings
        },
    }
    path = cfg.data_dir / f"snapshot-{int(time.time())}.json"
    write_json(path, snapshot)
    return path, snapshot, listings


def all_paths(cfg: Config) -> list[Path]:
    return sorted(cfg.data_dir.glob("snapshot-*.json"), key=lambda p: int(p.stem.split("-")[1]))


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Per album: item counts, added/removed ids, and whether removals are trustworthy."""
    result = []
    for album_id in sorted(set(old["albums"]) | set(new["albums"])):
        before = old["albums"].get(album_id)
        after = new["albums"].get(album_id)
        entry: dict[str, Any] = {"album": (after or before)["name"]}
        if before is None or after is None:
            entry["only_in"] = "new" if before is None else "old"
        else:
            old_ids = {i["id"] for i in before["items"]}
            new_ids = {i["id"] for i in after["items"]}
            entry.update(
                old_count=len(old_ids),
                new_count=len(new_ids),
                added=sorted(new_ids - old_ids),
                removed=sorted(old_ids - new_ids),
                reliable=before["complete"] and after["complete"],
            )
        result.append(entry)
    return result


def diff_latest(cfg: Config) -> Optional[tuple[Path, Path, list[dict[str, Any]]]]:
    paths = all_paths(cfg)
    if len(paths) < 2:
        return None
    old, new = read_json(paths[-2]), read_json(paths[-1])
    # Snapshots from before sources were pluggable have no "albums" key.
    if "albums" not in old or "albums" not in new:
        return None
    return paths[-2], paths[-1], diff(old, new)


def download_samples(source: Source, items: list[MediaItem], out_dir: Path, max_px: int) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for item in items:
        path = out_dir / f"{item.id[:32]}{'.mp4' if item.is_video else '.jpg'}"
        result: dict[str, Any] = {"id": item.id}
        try:
            source.fetch(item, path, max_px)
            result.update(file=path.name, kb=path.stat().st_size // 1024)
        except Exception as e:
            result["error"] = str(e)
        results.append(result)
    return results
