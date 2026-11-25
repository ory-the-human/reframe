"""The sync engine: makes each frame hold the photos of the albums it shows.

It only uses the `Source` and `Frame` interfaces (PLAN.md phase 2). Each run lists
every album that at least one frame shows, once, then for each frame adds what's
new and removes what has left the album. State lives in /data/state.sqlite, one
row per (frame, album, item).

Removals are deliberately slow, since a wrong one deletes a photo from a frame:
only complete listings count, an item must be missing from REMOVE_AFTER_RUNS of
them in a row, and if that would remove more than MAX_REMOVE_FRACTION of an album
at once, it waits MASS_EXTRA_RUNS more. Albums a frame stops showing are removed
from it straight away: that's the owner's choice, not a listing to doubt.
"""

import fcntl
import json
import re
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from . import frames as frames_mod
from .config import Config
from .frames import FrameConfig, FrameError
from .sources import Album, Listing, MediaItem, Source, get_source

REMOVE_AFTER_RUNS = 2
MAX_REMOVE_FRACTION = 0.25
MASS_EXTRA_RUNS = 2

Log = Callable[[str], None]

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    frame_id TEXT NOT NULL,
    album_id TEXT NOT NULL,
    media_id TEXT NOT NULL,
    frame_path TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    missing_runs INTEGER NOT NULL DEFAULT 0,
    taken TEXT,  -- RFC 3339, when the source knows it
    PRIMARY KEY (frame_id, album_id, media_id)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started REAL NOT NULL,
    finished REAL,
    summary TEXT
);
"""


class SyncBusy(RuntimeError):
    pass


@dataclass
class AlbumResult:
    album: str
    added: int = 0
    kept: int = 0
    removed: int = 0
    waiting: int = 0  # missing, not removed yet
    failed: int = 0
    note: str = ""


@dataclass
class FrameResult:
    frame: str
    albums: list[AlbumResult] = field(default_factory=list)
    error: str = ""


def _db(cfg: Config) -> sqlite3.Connection:
    db = sqlite3.connect(cfg.data_dir / "state.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    if "taken" not in {r["name"] for r in db.execute("PRAGMA table_info(items)")}:
        db.execute("ALTER TABLE items ADD COLUMN taken TEXT")
    return db


@contextmanager
def _exclusive(cfg: Config) -> Iterator[None]:
    """One sync at a time, also across processes (the web server and the CLI)."""
    with open(cfg.data_dir / "sync.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncBusy("a sync is already running")
        yield


def file_name(item: MediaItem) -> str:
    """The item's file name on a frame; the same item always gets the same one."""
    stem = re.sub(r"[^A-Za-z0-9_-]", "", item.id)[:64] or "item"
    return stem + (".mp4" if item.is_video else ".jpg")


def run(cfg: Config, source: Optional[Source] = None, dry_run: bool = False, log: Log = lambda s: None) -> dict[str, Any]:
    """Sync every frame once; returns the run's summary (also saved for the dashboard)."""
    with _exclusive(cfg):
        db = _db(cfg)
        started = time.time()
        run_id = None
        if not dry_run:
            run_id = db.execute("INSERT INTO runs (started) VALUES (?)", (started,)).lastrowid
            db.commit()
        try:
            results, error = _sync(cfg, db, source or get_source(cfg), dry_run, log), ""
        except Exception as e:
            db.rollback()
            results, error = [], str(e)
            log(f"sync failed: {e}")
        if dry_run:
            db.rollback()
        summary = {
            "started": started,
            "finished": time.time(),
            "dry_run": dry_run,
            "error": error,
            "frames": [
                {"frame": r.frame, "error": r.error, "albums": [a.__dict__ for a in r.albums]} for r in results
            ],
        }
        if run_id is not None:
            db.execute("UPDATE runs SET finished = ?, summary = ? WHERE id = ?", (summary["finished"], json.dumps(summary), run_id))
            db.execute("DELETE FROM runs WHERE id <= ?", (run_id - 50,))
            db.commit()
        db.close()
        return summary


def _sync(cfg: Config, db: sqlite3.Connection, source: Source, dry_run: bool, log: Log) -> list[FrameResult]:
    frames = frames_mod.load_all(cfg)
    # If this fails the run stops: without the album list nothing can be judged removed.
    albums = {a.id: a for a in source.albums()}

    # Rows of frames that no longer exist.
    known = {f.id for f in frames}
    for (frame_id,) in db.execute("SELECT DISTINCT frame_id FROM items").fetchall():
        if frame_id not in known:
            db.execute("DELETE FROM items WHERE frame_id = ?", (frame_id,))

    listings: dict[str, Optional[Listing]] = {}
    for album_id in sorted({a for f in frames for a in albums if f.shows(a)}):
        try:
            listings[album_id] = source.list_album(albums[album_id])
            log(f"{albums[album_id].name}: {len(listings[album_id].items)} items"
                + ("" if listings[album_id].complete else " (incomplete)"))
        except Exception as e:
            listings[album_id] = None
            log(f"{albums[album_id].name}: couldn't list it: {e}")

    results = []
    for frame in frames:
        adapter = frames_mod.build(cfg, frame)
        result = FrameResult(frame.name)
        try:
            _sync_frame(db, source, frame, adapter, albums, listings, result, dry_run, log)
        except FrameError as e:
            # Typically unreachable: try again next run, with the state as far as it got.
            result.error = str(e)
            log(f"{frame.name}: {e}")
        finally:
            if hasattr(adapter, "close"):
                adapter.close()
        if not dry_run:
            db.commit()
        results.append(result)
    return results


def _sync_frame(db, source, frame: FrameConfig, adapter, albums: dict[str, Album], listings, result: FrameResult,
                dry_run: bool, log: Log) -> None:
    now = time.time()
    rows = db.execute("SELECT * FROM items WHERE frame_id = ?", (frame.id,)).fetchall()
    paths = {r["media_id"]: r["frame_path"] for r in rows}  # files already on this frame
    users: dict[str, int] = {}  # frame_path -> rows using it
    for r in rows:
        users[r["frame_path"]] = users.get(r["frame_path"], 0) + 1

    def drop(album_id: str, media_id: str, path: str) -> None:
        db.execute("DELETE FROM items WHERE frame_id = ? AND album_id = ? AND media_id = ?", (frame.id, album_id, media_id))
        users[path] -= 1
        # The same photo in two albums shares one file.
        if users[path] == 0:
            del users[path]
            paths.pop(media_id, None)
            if not dry_run:
                adapter.remove(path)

    # Albums this frame no longer shows (or that were removed from the source).
    for album_id in sorted({r["album_id"] for r in rows} - {a for a in albums if frame.shows(a)}):
        gone = [r for r in rows if r["album_id"] == album_id]
        for r in gone:
            drop(album_id, r["media_id"], r["frame_path"])
        name = albums[album_id].name if album_id in albums else album_id
        result.albums.append(AlbumResult(album=name, removed=len(gone), note="no longer shown"))
        log(f"{frame.name}: removed {len(gone)} photos of an album it no longer shows")

    for album_id in sorted(a for a in albums if frame.shows(a)):
        album = albums[album_id]
        res = AlbumResult(album=album.name)
        result.albums.append(res)
        listing = listings.get(album_id)
        have = {r["media_id"]: r for r in rows if r["album_id"] == album_id}
        if listing is None:
            res.note = "couldn't list the album"
            continue

        for item in listing.items:
            if item.id in have:
                db.execute("UPDATE items SET last_seen = ?, missing_runs = 0, taken = ? WHERE frame_id = ? AND album_id = ? AND media_id = ?",
                           (now, item.created, frame.id, album_id, item.id))
                res.kept += 1
                continue
            path = paths.get(item.id)
            if path is None:
                try:
                    path = _copy(source, adapter, item, dry_run)
                except FrameError:
                    raise
                except Exception as e:
                    res.failed += 1
                    log(f"{frame.name}: couldn't copy {item.id}: {e}")
                    continue
                paths[item.id] = path
            users[path] = users.get(path, 0) + 1
            db.execute("INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, 0, ?)", (frame.id, album_id, item.id, path, now, now, item.created))
            res.added += 1

        listed = {i.id for i in listing.items}
        missing = [r for m, r in have.items() if m not in listed]
        # An empty listing of an album we hold photos of is more likely a glitch.
        if not listing.complete or (not listing.items and have):
            res.note = "listing incomplete: nothing removed"
            res.waiting = len(missing)
            continue
        for r in missing:
            db.execute("UPDATE items SET missing_runs = missing_runs + 1 WHERE frame_id = ? AND album_id = ? AND media_id = ?",
                       (frame.id, album_id, r["media_id"]))
        due = [r for r in missing if r["missing_runs"] + 1 >= REMOVE_AFTER_RUNS]
        if len(due) > MAX_REMOVE_FRACTION * len(have):
            sure = [r for r in due if r["missing_runs"] + 1 >= REMOVE_AFTER_RUNS + MASS_EXTRA_RUNS]
            if len(sure) < len(due):
                res.note = "many photos left at once: waiting to be sure"
            due = sure
        for r in due:
            drop(album_id, r["media_id"], r["frame_path"])
        res.removed = len(due)
        res.waiting = len(missing) - len(due)
        if res.added or res.removed or res.failed:
            log(f"{frame.name} / {album.name}: +{res.added} -{res.removed}"
                + (f", {res.failed} failed" if res.failed else "") + (f" ({res.note})" if res.note else ""))


def _copy(source: Source, adapter, item: MediaItem, dry_run: bool) -> str:
    name = file_name(item)
    if dry_run:
        return name
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / name
        source.fetch(item, local, adapter.max_px)
        return adapter.put(local, name)


def last_run(cfg: Config) -> Optional[dict[str, Any]]:
    """The latest finished run's summary, for the dashboard."""
    if not (cfg.data_dir / "state.sqlite").exists():
        return None
    db = _db(cfg)
    try:
        row = db.execute("SELECT summary FROM runs WHERE summary IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row["summary"]) if row else None
    finally:
        db.close()


def counts(cfg: Config) -> dict[str, int]:
    """Items on each frame (by frame id)."""
    if not (cfg.data_dir / "state.sqlite").exists():
        return {}
    db = _db(cfg)
    try:
        return {r[0]: r[1] for r in db.execute("SELECT frame_id, COUNT(DISTINCT frame_path) FROM items GROUP BY frame_id")}
    finally:
        db.close()


def taken(cfg: Config, frame_id: str) -> dict[str, str]:
    """When each file on a frame was taken (by file name), where known."""
    if not (cfg.data_dir / "state.sqlite").exists():
        return {}
    db = _db(cfg)
    try:
        rows = db.execute("SELECT frame_path, MAX(taken) FROM items WHERE frame_id = ? AND taken IS NOT NULL GROUP BY frame_path",
                          (frame_id,))
        return {Path(r[0]).name: r[1] for r in rows}
    finally:
        db.close()
