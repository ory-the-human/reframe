"""Web UI for setup, sign-in and the listing test, so the NAS needs no SSH.

Single-user and meant for the LAN. Set WEB_PASSWORD to require HTTP basic auth;
this UI can read the photos the source is authorized for.
"""

import collections
import hashlib
import zlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path
from functools import wraps
from typing import Any, Optional
from urllib.parse import urlparse

import qrcode
import qrcode.image.svg
import requests
from flask import Flask, Response, abort, flash, g, redirect, render_template, request, send_file, send_from_directory, url_for
from markupsafe import Markup
from PIL import Image, ImageOps

from . import agent, config, frames, snapshots, sources, sync
from .frames import kiosk, registry
from .frames import web as web_frame
from .sources import local, share_link
from .sources.google_ambient.api import AmbientClient
from .sources.google_ambient.auth import TokenStore
from .sources.google_ambient.login import LoginFlow
from .store import read_json

app = Flask(__name__)
# Open slideshows reload themselves when this differs from the version they were served.
PAGE_VERSION = hashlib.sha256((Path(__file__).parent / "templates" / "slideshow.html").read_bytes()).hexdigest()[:12]

_login_lock = threading.Lock()
_login: Optional[LoginFlow] = None
# Results of the last test run, kept in memory to render on the dashboard.
_last_listing: Optional[dict[str, Any]] = None
_frame_checks: dict[str, dict[str, Any]] = {}  # by frame id


def _secret_key() -> bytes:
    path = config.data_dir() / "web_secret"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(32))
    return path.read_bytes()


@app.before_request
def _protect() -> Optional[Response]:
    # Kiosks can't answer a password prompt; slideshow URLs carry their own
    # secret, and frame agents send their key.
    if request.path.startswith(("/show/", "/agent/")):
        return None
    password = os.environ.get("WEB_PASSWORD")
    if password:
        auth = request.authorization
        if not (auth and auth.password and hmac.compare_digest(auth.password, password)):
            return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="reframe"'})
    # Reject cross-site form posts: any page on the internet could otherwise
    # submit to this LAN address from the user's browser.
    if request.method == "POST":
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if origin and urlparse(origin).netloc != request.host:
            return Response("Cross-origin request rejected", 403)
    return None


def _qr_svg(data: str) -> Markup:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return Markup(img.to_string(encoding="unicode"))


app.jinja_env.globals.update(qr_svg=_qr_svg, duration=agent.duration, clock=agent.clock, sparkline=agent.sparkline,
                              adb_states=agent.ADB_STATES)
app.jinja_env.filters["hue"] = lambda text: zlib.crc32(text.encode()) % 360


def _guard(view):
    """Turn API/adb exceptions into a flash message instead of a 500 page."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except Exception as e:
            flash(f"{type(e).__name__}: {e}", "error")
            return redirect(request.referrer or url_for("index"))

    return wrapper


def _account_status(cfg: config.Config) -> dict[str, Any]:
    token = read_json(cfg.token_file)
    status: dict[str, Any] = {"signed_in": token is not None, "device": None, "device_error": None}
    if token and "first_obtained_at" in token:
        status["token_age_days"] = (time.time() - token["first_obtained_at"]) / 86400
    saved = read_json(cfg.device_file)
    if token and saved:
        try:
            # devices.get doesn't count toward the 240/day mediaItems.list quota.
            status["device"] = AmbientClient(TokenStore(cfg)).get_device(saved["id"])
        except Exception as e:
            status["device_error"] = str(e)
    return status


def _account(cfg: config.Config) -> Optional[dict[str, Any]]:
    # Sign-in state is specific to the Google Photos Ambient source.
    return _account_status(cfg) if cfg.source == "google_ambient" and cfg.has_client else None


@app.context_processor
def _sidebar() -> dict[str, Any]:
    if request.path.startswith(("/show/", "/agent/")):
        return {}
    return {"nav": _views(config.load())}


@app.get("/")
def index():
    cfg = config.load()
    return render_template(
        "index.html",
        cfg=cfg,
        account=_account(cfg),
        sync_running=_sync_lock.locked(),
        sync_choices=config.SYNC_CHOICES,
        now=time.time(),
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = config.load()
    if request.method == "POST":
        updates = {k: request.form[k].strip() for k in ("source", "client_id", "device_name", "public_url", "sync_minutes") if k in request.form}
        try:
            updates.update(_settings_from_form())
        except frames.FrameError as e:
            flash(str(e), "error")
            return redirect(request.referrer or url_for("settings"))
        # The secret is write-only: blank means keep the current one.
        if request.form.get("client_secret", "").strip():
            updates["client_secret"] = request.form["client_secret"].strip()
        config.save_settings(updates)
        return redirect(request.referrer or url_for("settings"))
    return render_template(
        "settings.html",
        cfg=cfg,
        source_kinds=sources.KINDS,
        settings=config.FRAME_SETTINGS,
        account=_account(cfg),
        login=_login,
        listing=_last_listing,
        latest_diff=snapshots.diff_latest(cfg),
    )


@app.post("/albums/add")
def album_add():
    try:
        a = share_link.add(config.load(), request.form.get("url", ""), request.form.get("name", ""))
    except (share_link.ShareLinkError, requests.RequestException) as e:
        flash(f"Couldn't add the album: {e}", "error")
        return redirect(url_for("index"))
    flash(f"Added {a['name']}. Frames that show every album get it at the next sync.", "ok")
    return redirect(url_for("album_page", album_id=a["id"]))


@app.post("/albums/new")
def album_new():
    a = local.add(config.load(), request.form.get("name", ""))
    flash(f"Added {a['name']}. Upload its photos here.", "ok")
    return redirect(url_for("album_page", album_id=a["id"]))


def _albums_module(album_id: str):
    return local if album_id.startswith(local.PREFIX) else share_link


@app.post("/albums/<album_id>/rename")
def album_rename(album_id: str):
    try:
        _albums_module(album_id).rename(config.load(), album_id, request.form.get("name", ""))
        flash("Renamed.", "ok")
    except (share_link.ShareLinkError, local.LocalAlbumError) as e:
        flash(str(e), "error")
    return redirect(url_for("album_page", album_id=album_id))


@app.post("/albums/<album_id>/remove")
def album_remove(album_id: str):
    try:
        a = _albums_module(album_id).remove(config.load(), album_id)
        flash(f"Removed {a['name']}. The next sync takes its photos off the frames.", "ok")
    except (share_link.ShareLinkError, local.LocalAlbumError) as e:
        flash(str(e), "error")
    return redirect(url_for("index"))


def _local_album(album_id: str) -> tuple[config.Config, dict[str, str]]:
    cfg = config.load()
    try:
        return cfg, local.find(cfg, album_id)
    except local.LocalAlbumError:
        abort(404)


@app.get("/albums/<album_id>")
def album_page(album_id: str):
    cfg = config.load()
    album = next((a for a in _views(cfg)["albums"] if a["id"] == album_id), None) or abort(404)
    if album["kind"] == "local":
        items = [{"name": p.name, "video": mime.startswith("video/"), "thumb": None if mime.startswith("video/") else _thumb_url(cfg, p),
                  "url": url_for("album_file", album_id=album_id, name=p.name)} for p, mime in local.files(cfg, album_id)]
    else:
        items = [{"name": p.name, "thumb": _thumb_url(cfg, p)} for p in album["photos"] if p.suffix.lower() in IMAGE_SUFFIXES]
    return render_template("album.html", album=album, items=items)


@app.post("/albums/<album_id>/upload")
def album_upload(album_id: str):
    cfg, album = _local_album(album_id)
    uploads = [f for f in request.files.getlist("files") if f.filename and local.media_type(f.filename)]
    for f in uploads:
        local.save_upload(cfg, album_id, f)
    flash(f"Uploaded {len(uploads)} files. Frames get them at the next sync.", "ok")
    return redirect(url_for("album_page", album_id=album_id))


@app.get("/albums/<album_id>/files/<name>")
def album_file(album_id: str, name: str):
    cfg, _ = _local_album(album_id)
    return send_from_directory(local.album_dir(cfg, album_id), name)


@app.post("/albums/<album_id>/files/<name>/delete")
def album_file_delete(album_id: str, name: str):
    cfg, _ = _local_album(album_id)
    (local.album_dir(cfg, album_id) / name).unlink(missing_ok=True)
    flash(f"Deleted {name}. The next sync takes it off the frames.", "ok")
    return redirect(url_for("album_page", album_id=album_id))


# Held while this process syncs; sync.run's file lock also covers the CLI.
_sync_lock = threading.Lock()
_last_sync = 0.0


def _run_sync(reason: str) -> None:
    global _last_sync
    if not _sync_lock.acquire(blocking=False):
        return
    try:
        _last_sync = time.time()
        summary = sync.run(config.load(), log=print)
        failed = sum(1 for f in summary["frames"] if f["error"])
        print(f"sync ({reason}): " + (f"failed: {summary['error']}" if summary["error"]
                                       else f"{len(summary['frames'])} frames, {failed} with errors"))
    except sync.SyncBusy:
        print(f"sync ({reason}) skipped: a sync is already running")
    finally:
        _sync_lock.release()


def _sync_loop() -> None:
    time.sleep(30)
    while True:
        try:
            interval = config.load().sync_interval
            if interval and time.time() - _last_sync >= interval:
                _run_sync("scheduled")
        except Exception as e:
            print(f"sync loop: {type(e).__name__}: {e}")
        time.sleep(60)


@app.post("/sync")
def sync_now():
    if not _sync_lock.locked():
        threading.Thread(target=_run_sync, args=("now",), daemon=True).start()
        flash("Sync started.", "ok")
    return redirect(url_for("index"))


@app.post("/login/start")
@_guard
def login_start():
    global _login
    cfg = config.load()
    if not cfg.has_client:
        flash("Add the Google OAuth client ID and secret first.", "error")
        return redirect(url_for("settings"))
    with _login_lock:
        if _login is None or not _login.active:
            _login = LoginFlow(cfg, new_device=request.form.get("new_device") == "1")
            _login.start_in_background()
    return redirect(url_for("settings"))


@app.get("/login/status")
def login_status():
    # Polled by the dashboard while a sign-in is in progress.
    return {"step": _login.step if _login else None}


@app.post("/login/dismiss")
def login_dismiss():
    global _login
    with _login_lock:
        if _login is not None and not _login.active:
            _login = None
    return redirect(url_for("settings"))


@app.post("/signout")
@_guard
def signout():
    cfg = config.load()
    if request.form.get("forget_device") == "1":
        saved = read_json(cfg.device_file)
        if saved:
            AmbientClient(TokenStore(cfg)).delete_device(saved["id"])
            cfg.device_file.unlink()
    cfg.token_file.unlink(missing_ok=True)
    flash("Signed out.", "ok")
    return redirect(url_for("settings"))


@app.post("/test/list")
@_guard
def test_list():
    global _last_listing
    cfg = config.load()
    source = sources.get_source(cfg)
    _, snapshot, listings = snapshots.take(cfg, source, source.albums())

    n_download = int(request.form.get("download") or 0)
    max_px = frames.largest_max_px(cfg) if n_download else 0
    _last_listing = {
        "taken_at": snapshot["taken_at"],
        "albums": [
            {
                "name": listing.album.name,
                "count": len(listing.items),
                "complete": listing.complete,
                "mimes": collections.Counter(i.mime_type for i in listing.items).most_common(),
                "samples": snapshots.download_samples(
                    source, listing.items[:n_download], cfg.data_dir / "samples", max_px
                )
                if n_download
                else [],
            }
            for listing in listings
        ],
    }
    return redirect(url_for("settings") + "#test")


FRAME_KINDS = {"cozyla_adb": "Cozyla frame", "folder": "Folder", "web": "Browser slideshow"}
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")
THUMB_DIRS = ("albums", "frames", "agents")


def _thumb_url(cfg: config.Config, path: Optional[Path]) -> Optional[str]:
    if not path:
        return None
    return url_for("thumb", rel=path.relative_to(cfg.data_dir).as_posix(), t=int(path.stat().st_mtime))


def _first_image(paths) -> Optional[Path]:
    return next((p for p in paths if p.suffix.lower() in IMAGE_SUFFIXES), None)


@app.get("/thumb/<path:rel>")
def thumb(rel: str):
    cfg = config.load()
    src = (cfg.data_dir / rel).resolve()
    if not src.is_file() or not any(src.is_relative_to((cfg.data_dir / d).resolve()) for d in THUMB_DIRS):
        abort(404)
    out = cfg.data_dir / "thumbs" / f"{hashlib.sha1(rel.encode()).hexdigest()}.jpg"
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        out.parent.mkdir(exist_ok=True)
        with Image.open(src) as img:
            small = ImageOps.exif_transpose(img)
            small.thumbnail((240, 240))
            small.convert("RGB").save(out, "JPEG", quality=80)
    return send_file(out, max_age=86400)


def _views(cfg: config.Config) -> dict[str, Any]:
    """Every frame and album as the pages and the sidebar show them, built once per request."""
    if "views" not in g:
        all_frames = frames.load_all(cfg)
        albums, error = _all_albums(cfg)
        run = sync.last_run(cfg)
        album_views = _album_views(cfg, albums, all_frames)
        counts = sync.counts(cfg)
        g.views = {"frames": [_frame_view(cfg, f, run, counts, album_views) for f in all_frames],
                   "albums": album_views, "albums_error": error, "run": run}
    return g.views


def _frame_status(frame: registry.FrameConfig, health: Optional[dict[str, Any]], synced: Optional[dict[str, Any]]) -> tuple[str, str]:
    if synced and synced["error"]:
        return "error", "Sync failed"
    failed = sum(a["failed"] for a in synced["albums"]) if synced else 0
    if failed:
        return "warn", f"{failed} photo{'s' * (failed != 1)} failed to sync"
    if frame.agent_key:
        if not health:
            return "off", "No report yet"
        return ("ok", "Online") if health["online"] else ("off", f"Offline, seen {agent.duration(health['age'])} ago")
    return ("ok", "Synced") if synced else ("off", "Not synced yet")


def _frame_view(cfg: config.Config, frame: registry.FrameConfig, run: Optional[dict[str, Any]], counts: dict[str, int],
                albums: list[dict[str, Any]]) -> dict[str, Any]:
    health = agent.health(cfg, frame) if frame.agent_key else None
    synced = next((r for r in run["frames"] if r["frame"] == frame.name), None) if run else None
    shot = agent.screenshot_file(cfg, frame.id)
    media = registry.web_media_dir(cfg.data_dir, frame.id)
    own = _first_image(media / m["name"] for m in web_frame.list_media(media))
    cover = next((a["thumb"] for a in albums if a["thumb"] and frame.shows(a["id"])), None)
    kind = FRAME_KINDS.get(frame.kind, frame.kind)
    count, n = counts.get(frame.id, 0), len(frame.albums)
    return {
        "frame": frame,
        "kind_label": kind,
        "subtitle": f"{kind}, {count} photos from " + (f"{n} album{'s' * (n != 1)}" if n else "every album"),
        "status": _frame_status(frame, health, synced),
        "synced": synced,
        "count": count,
        "thumb": _thumb_url(cfg, shot if shot.exists() else own) or cover,
        "slideshow_url": url_for("slideshow", key=frame.key, _external=True) if frame.shows_slideshow else None,
        "check": _frame_checks.get(frame.id),
        "health": health,
    }


def _album_choices(cfg: config.Config, frame: Optional[registry.FrameConfig]) -> tuple[list[dict[str, str]], Optional[str]]:
    """Albums a frame can pick from, plus an error if the source couldn't be asked."""
    saved = frame.albums if frame else []
    try:
        offered = [{"id": a.id, "name": a.name} for a in sources.get_source(cfg).albums()]
    except Exception as e:
        return saved, str(e)
    # Keep picks the source no longer offers visible, so they can be unticked.
    known = {a["id"] for a in offered}
    return offered + [a for a in saved if a["id"] not in known], None


def _all_albums(cfg: config.Config) -> tuple[list[dict[str, str]], Optional[str]]:
    """Every album frames can show, plus an error; only the local ones if the source can't be asked."""
    albums, error = _album_choices(cfg, None)
    return (local.saved_albums(cfg) if error else albums), error


ALBUM_KINDS = {"local": "Uploaded to this server", "share_link": "Shared Google Photos album", "google_ambient": "Google Photos album"}


def _album_views(cfg: config.Config, albums: list[dict[str, str]], all_frames: list[registry.FrameConfig]) -> list[dict[str, Any]]:
    counts, synced = sync.album_counts(cfg), sync.album_files(cfg)
    views = []
    for a in albums:
        kind = "local" if a["id"].startswith(local.PREFIX) else cfg.source
        photos = [p for p, _ in local.files(cfg, a["id"])] if kind == "local" else synced.get(a["id"], [])
        views.append({**a, "kind": kind, "kind_label": ALBUM_KINDS[kind], "photos": photos,
                      "count": len(photos) if kind == "local" else counts.get(a["id"]),
                      "thumb": _thumb_url(cfg, _first_image(photos)),
                      "frames": [f for f in all_frames if f.shows(a["id"])]})
    return views


def _show_album(frame: registry.FrameConfig, album: dict[str, str], show: bool, all_albums: list[dict[str, str]]) -> None:
    """Show or hide one album on a frame; hiding one from an all-albums frame lists the others."""
    if not show:
        frame.albums = [a for a in frame.albums or all_albums if a["id"] != album["id"]]
    elif album["id"] not in frame.album_ids:
        frame.albums.append(album)


def _settings_from_form() -> dict[str, str]:
    """The frame settings a form carries, validated; settings it doesn't carry are left out."""
    values = {}
    for setting in config.FRAME_SETTINGS:
        value = setting.from_form(request.form)
        if value is not None:
            error = setting.check(value)
            if error:
                raise frames.FrameError(error)
            values[setting.key] = value
    return values


def _frame_from_form(cfg: config.Config, frame: registry.FrameConfig) -> registry.FrameConfig:
    for field in ("name", "address", "folder"):
        if field in request.form:
            setattr(frame, field, request.form[field].strip())
    frame.display.update(_settings_from_form())
    # Only values that differ from the default are the frame's own.
    frame.display = {k: v for k, v in frame.display.items() if v and v != cfg.defaults.get(k)}
    return frame


def _frame_form(cfg: config.Config, frame: registry.FrameConfig):
    job = _kiosk_jobs.get(frame.id)
    if job and job.get("done"):
        _kiosk_jobs.pop(frame.id, None)
        flash(job.get("error") or job.get("result", ""), "error" if job.get("error") else "ok")
        job = None
    views = _views(cfg)
    by_id = {a["id"]: a for a in views["albums"]}
    choices, albums_error = _album_choices(cfg, frame)
    return render_template(
        "frame.html",
        frame=frame,
        view=next(v for v in views["frames"] if v["frame"].id == frame.id),
        kiosk_job=job,
        albums=[{**by_id.get(a["id"], {}), **a} for a in choices],
        albums_error=albums_error,
        settings=config.settings_for(frame),
        values=registry.settings(cfg, frame),
        screenshot=agent.screenshot(cfg, frame) if frame.agent_key else None,
        reboot_requested=bool(frame.agent_key) and agent.requested(cfg, frame, "reboot"),
        kiosk_home=agent.home_url(cfg, frame) if frame.is_kiosk else None,
    )


@app.route("/frames/new", methods=["GET", "POST"])
def frame_new():
    cfg = config.load()
    kind = request.values.get("kind", "web")
    frame = registry.FrameConfig(id="", name="", kind=kind if kind in registry.KINDS else "web")
    if request.method == "POST":
        try:
            _frame_from_form(cfg, frame)
            added = registry.add(cfg, frame.name, frame.kind, address=frame.address, folder=frame.folder)
        except frames.FrameError as e:
            flash(str(e), "error")
        else:
            flash(f"Added {added.name}. It shows every album; pick some below to show only those.", "ok")
            return redirect(url_for("frame_edit", frame_id=added.id))
    return render_template("frame_new.html", frame=frame, kinds=registry.KINDS)


def _find_frame(cfg: config.Config, frame_id: str) -> registry.FrameConfig:
    try:
        return registry.find(frames.load_all(cfg), frame_id)
    except frames.FrameError:
        abort(404)


@app.route("/frames/<frame_id>", methods=["GET", "POST"])
def frame_edit(frame_id: str):
    cfg = config.load()
    frame = _find_frame(cfg, frame_id)
    if request.method == "POST":
        try:
            _frame_from_form(cfg, frame)
            registry.update(cfg, frame)
        except frames.FrameError as e:
            flash(str(e), "error")
        return redirect(request.referrer or url_for("frame_edit", frame_id=frame_id))
    return _frame_form(cfg, frame)


def _switched(name: str) -> bool:
    # A switch posts a hidden "no" before its checkbox's "yes".
    return request.form.getlist(name)[-1:] == ["yes"]


@app.post("/frames/<frame_id>/albums")
def frame_albums(frame_id: str):
    """Show every album, or show or hide one; the frame and album pages both post here."""
    cfg = config.load()
    frame = _find_frame(cfg, frame_id)
    back = request.referrer or url_for("frame_edit", frame_id=frame_id)
    albums, error = _album_choices(cfg, frame)
    every = "every" in request.form
    album = None if every else next((a for a in albums if a["id"] == request.form.get("album")), None) or abort(404)
    on = _switched("every" if every else "show")
    if error and not on and (every or not frame.albums):
        # A list made now would leave out the albums the source didn't return.
        flash(f"Couldn't load albums from the source: {error}", "error")
    elif album and not on and frame.album_ids == {album["id"]}:
        flash(f"{frame.name} shows only this album; turn on another album or Every album first.", "error")
    else:
        if every:
            frame.albums = [] if on else albums
        else:
            _show_album(frame, album, on, albums)
        registry.update(cfg, frame)
    return redirect(back)


@app.post("/frames/<frame_id>/delete")
def frame_delete(frame_id: str):
    cfg = config.load()
    try:
        registry.remove(cfg, frame_id)
    except frames.FrameError as e:
        flash(str(e), "error")
        return redirect(url_for("frame_edit", frame_id=frame_id))
    _frame_checks.pop(frame_id, None)
    flash("Frame removed.", "ok")
    return redirect(url_for("index"))


@app.post("/frames/<frame_id>/screenshot")
def frame_screenshot(frame_id: str):
    cfg = config.load()
    frame = _find_frame(cfg, frame_id)
    agent.request(cfg, frame, "screenshot")
    flash("Asked the frame for a screenshot. It arrives within a minute; reload this page.", "ok")
    return redirect(url_for("frame_edit", frame_id=frame_id))


@app.post("/frames/<frame_id>/reboot")
def frame_reboot(frame_id: str):
    cfg = config.load()
    frame = _find_frame(cfg, frame_id)
    agent.request(cfg, frame, "reboot")
    flash("Asked the frame to reboot.", "ok")
    return redirect(url_for("frame_edit", frame_id=frame_id))


@app.get("/frames/<frame_id>/screenshot.png")
def frame_screenshot_png(frame_id: str):
    path = agent.screenshot_file(config.load(), frame_id)
    return send_from_directory(path.parent, path.name, max_age=0)


@app.post("/frames/<frame_id>/check")
def frame_check(frame_id: str):
    cfg = config.load()
    try:
        frame = registry.find(frames.load_all(cfg), frame_id)
        _frame_checks[frame_id] = {"ok": True, "info": frames.build(cfg, frame).check()}
    except Exception as e:
        _frame_checks[frame_id] = {"ok": False, "error": str(e)}
    return redirect(url_for("frame_edit", frame_id=frame_id))


def _public_base(cfg: config.Config) -> str:
    """The server's address as frames see it, for links written onto them."""
    if cfg.public_url:
        return cfg.public_url.rstrip("/")
    host = urlparse(request.host_url).hostname or ""
    if host in ("localhost", "127.0.0.1", "::1"):
        raise frames.FrameError(
            "this page was opened as localhost, which a frame can't reach: set "
            "\"Address frames use to reach this server\" in Settings, or open this page by its network address"
        )
    return request.host_url.rstrip("/")


# Kiosk setups and restores in progress or just finished, by frame id. They take
# about a minute, so they run in the background and the frame's page polls them.
_kiosk_jobs: dict[str, dict[str, Any]] = {}
_kiosk_lock = threading.Lock()


def _run_kiosk_job(cfg: config.Config, frame: registry.FrameConfig, deprovisioning: bool, server: str) -> None:
    job = _kiosk_jobs[frame.id]

    def progress(step: str, pct: int) -> None:
        job.update(step=step, pct=pct)

    def save(state: dict[str, Any]) -> None:
        frame.kiosk = state
        registry.update(cfg, frame)

    try:
        if deprovisioning:
            kiosk.deprovision(cfg, frame.id, frame.address, frame.kiosk, progress)
            save({})
            job["result"] = f"{frame.name} is back to the Cozyla app."
        else:
            agent_key = agent.ensure_key(cfg, frame)
            save(kiosk.provision(cfg, frame.id, frame.address, server, frame.key, agent_key, frame.kiosk, save, progress))
            job["result"] = f"{frame.name} is now a reframe kiosk."
    except Exception as e:
        job["error"] = f"{type(e).__name__}: {e}"
    finally:
        job.update(pct=100, done=True)


def _kiosk_action(frame_id: str, deprovisioning: bool):
    cfg = config.load()
    frame = _find_frame(cfg, frame_id)
    if frame.kind != "cozyla_adb":
        abort(400)
    server = ""
    if not deprovisioning:
        if not frame.key:
            frame.key = secrets.token_urlsafe(18)
            registry.update(cfg, frame)
        try:
            server = _public_base(cfg)
        except frames.FrameError as e:
            flash(str(e), "error")
            return redirect(url_for("frame_edit", frame_id=frame.id))
    with _kiosk_lock:
        running = _kiosk_jobs.get(frame.id)
        if not (running and not running.get("done")):
            _kiosk_jobs[frame.id] = {
                "action": "restore" if deprovisioning else "setup",
                "step": "Starting",
                "pct": 0,
                "done": False,
            }
            threading.Thread(target=_run_kiosk_job, args=(cfg, frame, deprovisioning, server), daemon=True).start()
    return redirect(url_for("frame_edit", frame_id=frame.id))


@app.get("/frames/<frame_id>/kiosk/status")
def frame_kiosk_status(frame_id: str):
    return _kiosk_jobs.get(frame_id) or {}


@app.post("/frames/<frame_id>/kiosk/provision")
def frame_kiosk_provision(frame_id: str):
    return _kiosk_action(frame_id, deprovisioning=False)


@app.post("/frames/<frame_id>/kiosk/deprovision")
def frame_kiosk_deprovision(frame_id: str):
    return _kiosk_action(frame_id, deprovisioning=True)


def _web_frame(key: str) -> tuple[config.Config, registry.FrameConfig]:
    cfg = config.load()
    frame = registry.by_key(frames.load_all(cfg), key)
    if frame is None:
        abort(404)
    return cfg, frame


@app.get("/show/<key>")
def slideshow(key: str):
    _web_frame(key)
    return render_template("slideshow.html", key=key, page_version=PAGE_VERSION)


@app.get("/show/<key>/playlist")
def slideshow_playlist(key: str):
    # Polled by the slideshow, so new photos and setting changes show up without a reload.
    return _playlist(*_web_frame(key))


def _playlist(cfg: config.Config, frame: registry.FrameConfig) -> dict[str, Any]:
    media = web_frame.list_media(registry.web_media_dir(cfg.data_dir, frame.id))
    taken = sync.taken(cfg, frame.id)
    return {"display": frames.display_for(cfg, frame), "page_version": PAGE_VERSION, "items": [{**m, "taken": taken.get(m["name"])} for m in media]}


@app.get("/show/<key>/media/<name>")
def slideshow_media(key: str, name: str):
    cfg, frame = _web_frame(key)
    return send_from_directory(registry.web_media_dir(cfg.data_dir, frame.id), name, max_age=86400)


def _agent_frame() -> tuple[config.Config, registry.FrameConfig]:
    cfg = config.load()
    scheme, _, key = request.headers.get("Authorization", "").partition(" ")
    frame = registry.by_agent_key(frames.load_all(cfg), key) if scheme.lower() == "bearer" and key else None
    if frame is None:
        abort(401)
    return cfg, frame


@app.get("/agent/v1/config")
def agent_config():
    cfg, frame = _agent_frame()
    return agent.agent_config(cfg, frame)


@app.get("/agent/v1/apk")
def agent_apk():
    cfg, _ = _agent_frame()
    return send_file(kiosk.agent_apk(cfg), mimetype="application/vnd.android.package-archive")


@app.post("/agent/v1/report")
def agent_report():
    cfg, frame = _agent_frame()
    if (request.content_length or 0) > agent.MAX_REPORT_BYTES:
        abort(413)
    report = request.get_json(silent=True)
    if not isinstance(report, dict):
        abort(400)
    agent.save_report(cfg, frame, report)
    # The agent fetches its config again when this changes.
    reply = {"ok": True, "config_version": agent.agent_config(cfg, frame)["version"]}
    for what in ("screenshot", "reboot"):
        if agent.wanted(cfg, frame, what):
            reply[what] = True
    return reply


@app.get("/agent/v1/slideshow")
def agent_slideshow():
    # The agent keeps a copy of this and the files, and serves the slideshow on the frame.
    cfg, frame = _agent_frame()
    return {**_playlist(cfg, frame), "page": render_template("slideshow.html", key=frame.key, page_version=PAGE_VERSION)}


@app.get("/agent/v1/media/<name>")
def agent_media(name: str):
    cfg, frame = _agent_frame()
    return send_from_directory(registry.web_media_dir(cfg.data_dir, frame.id), name)


@app.post("/agent/v1/display")
def agent_display():
    # Set on the frame's own settings panel; the server stays the source of truth.
    cfg, frame = _agent_frame()
    changes = request.get_json(silent=True)
    if not isinstance(changes, dict):
        abort(400)
    agent.save_display(cfg, frame, changes)
    return {"ok": True}


@app.post("/agent/v1/screenshot")
def agent_screenshot():
    cfg, frame = _agent_frame()
    if (request.content_length or 0) > agent.MAX_SCREENSHOT_BYTES:
        abort(413)
    agent.save_screenshot(cfg, frame, request.get_data())
    return {"ok": True}


def serve(host: str, port: int) -> None:
    from waitress import serve as waitress_serve

    app.secret_key = _secret_key()
    threading.Thread(target=_sync_loop, daemon=True).start()
    print(f"reframe web UI on http://{host}:{port}")
    # Waitress caps request bodies at 1 GiB; a few phone videos in one upload can be more.
    waitress_serve(app, host=host, port=port, threads=4, max_request_body_size=10 << 30)
