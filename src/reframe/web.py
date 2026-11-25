"""Web UI for setup, sign-in and the listing test, so the NAS needs no SSH.

Single-user and meant for the LAN. Set WEB_PASSWORD to require HTTP basic auth;
this UI can read the photos the source is authorized for.
"""

import collections
import hmac
import os
import secrets
import threading
import time
from functools import wraps
from typing import Any, Optional
from urllib.parse import urlparse

import qrcode
import qrcode.image.svg
from flask import Flask, Response, abort, flash, redirect, render_template, request, send_from_directory, url_for
from markupsafe import Markup

from . import agent, config, frames, snapshots, sources
from .frames import kiosk, registry
from .frames import web as web_frame
from .sources.google_ambient.api import AmbientClient
from .sources.google_ambient.auth import TokenStore
from .sources.google_ambient.login import LoginFlow
from .store import read_json

app = Flask(__name__)

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


def _guard(view):
    """Turn API/adb exceptions into a flash message instead of a 500 page."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except Exception as e:
            flash(f"{type(e).__name__}: {e}", "error")
            return redirect(url_for("index"))

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


@app.get("/")
def index():
    cfg = config.load()
    return render_template(
        "index.html",
        cfg=cfg,
        # Sign-in state is specific to the Google Photos Ambient source.
        account=_account_status(cfg) if cfg.source == "google_ambient" and cfg.has_client else None,
        login=_login,
        listing=_last_listing,
        latest_diff=snapshots.diff_latest(cfg),
        frames=[_frame_view(cfg, f) for f in frames.load_all(cfg)],
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = config.load()
    if request.method == "POST":
        fields = ("source", "client_id", "device_name", "public_url", *config.DISPLAY_KEYS)
        updates = {k: request.form.get(k, "").strip() for k in fields}
        try:
            updates.update({k: "" for k in config.SCREEN_KEYS} | _screen_from_form())
        except frames.FrameError as e:
            flash(str(e), "error")
            return redirect(url_for("settings"))
        # The secret is write-only: blank means keep the current one.
        if request.form.get("client_secret", "").strip():
            updates["client_secret"] = request.form["client_secret"].strip()
        config.save_settings(updates)
        flash("Settings saved.", "ok")
        return redirect(url_for("index"))
    return render_template(
        "settings.html",
        cfg=cfg,
        source_kinds=sources.KINDS,
        display_choices=config.DISPLAY_CHOICES,
        screen_choices=config.SCREEN_CHOICES,
    )


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
    return redirect(url_for("index"))


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
    return redirect(url_for("index"))


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
    return redirect(url_for("index"))


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
    return redirect(url_for("index") + "#test")


def _frame_view(cfg: config.Config, frame: registry.FrameConfig) -> dict[str, Any]:
    return {
        "frame": frame,
        "kind_label": registry.KINDS.get(frame.kind, frame.kind),
        "display": frames.display_for(cfg, frame),
        "slideshow_url": url_for("slideshow", key=frame.key, _external=True) if frame.shows_slideshow else None,
        "check": _frame_checks.get(frame.id),
        "health": agent.health(cfg, frame) if frame.agent_key else None,
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


def _screen_from_form() -> dict[str, str]:
    """Screen settings from a form; blank fields are left out (the default)."""
    form = request.form
    values = {}
    mode = form.get("screen_schedule_mode", "")
    if mode == "always":
        values["screen_schedule"] = "always"
    elif mode == "off":
        values["screen_schedule"] = f"{form.get('screen_off_at', '')}-{form.get('screen_on_at', '')}"
    for key in config.SCREEN_KEYS[1:]:
        if form.get(key, "").strip():
            values[key] = form[key].strip()
    for key, value in values.items():
        error = config.check_screen(key, value)
        if error:
            raise frames.FrameError(error)
    return values


def _frame_from_form(frame: registry.FrameConfig) -> registry.FrameConfig:
    form = request.form
    frame.name = form.get("name", "").strip()
    frame.address = form.get("address", "").strip()
    frame.folder = form.get("folder", "").strip()
    frame.albums = [{"id": a, "name": form.get(f"album_name_{a}", a)} for a in form.getlist("album")]
    frame.display = {k: form.get(k, "").strip() for k in config.DISPLAY_KEYS if form.get(k, "").strip()}
    if frame.is_kiosk:
        frame.display.update(_screen_from_form())
    return frame


def _frame_form(cfg: config.Config, frame: registry.FrameConfig, is_new: bool):
    albums, albums_error = _album_choices(cfg, None if is_new else frame)
    job = _kiosk_jobs.get(frame.id)
    if job and job.get("done"):
        _kiosk_jobs.pop(frame.id, None)
        flash(job.get("error") or job.get("result", ""), "error" if job.get("error") else "ok")
        job = None
    return render_template(
        "frame.html",
        cfg=cfg,
        frame=frame,
        kiosk_job=job,
        is_new=is_new,
        kinds=registry.KINDS,
        albums=albums,
        albums_error=albums_error,
        display_choices=config.DISPLAY_CHOICES,
        screen_choices=config.SCREEN_CHOICES,
        screen=frames.screen_for(cfg, frame),
        health=agent.health(cfg, frame) if frame.agent_key else None,
    )


@app.route("/frames/new", methods=["GET", "POST"])
def frame_new():
    cfg = config.load()
    kind = request.values.get("kind", "web")
    frame = registry.FrameConfig(id="", name="", kind=kind if kind in registry.KINDS else "web")
    if request.method == "POST":
        try:
            _frame_from_form(frame)
            fields = {k: getattr(frame, k) for k in ("address", "folder", "albums", "display")}
            added = registry.add(cfg, frame.name, frame.kind, **fields)
        except frames.FrameError as e:
            flash(str(e), "error")
            return _frame_form(cfg, frame, is_new=True)
        flash(f"Added {added.name}.", "ok")
        return redirect(url_for("index") + "#frames")
    return _frame_form(cfg, frame, is_new=True)


@app.route("/frames/<frame_id>", methods=["GET", "POST"])
def frame_edit(frame_id: str):
    cfg = config.load()
    try:
        frame = registry.find(frames.load_all(cfg), frame_id)
    except frames.FrameError:
        abort(404)
    if request.method == "POST":
        try:
            _frame_from_form(frame)
            registry.update(cfg, frame)
        except frames.FrameError as e:
            flash(str(e), "error")
            return _frame_form(cfg, frame, is_new=False)
        flash(f"Saved {frame.name}.", "ok")
        return redirect(url_for("index") + "#frames")
    return _frame_form(cfg, frame, is_new=False)


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
    return redirect(url_for("index") + "#frames")


@app.post("/frames/<frame_id>/check")
def frame_check(frame_id: str):
    cfg = config.load()
    try:
        frame = registry.find(frames.load_all(cfg), frame_id)
        _frame_checks[frame_id] = {"ok": True, "info": frames.build(cfg, frame).check()}
    except Exception as e:
        _frame_checks[frame_id] = {"ok": False, "error": str(e)}
    return redirect(url_for("index") + "#frames")


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
    try:
        frame = registry.find(frames.load_all(cfg), frame_id)
    except frames.FrameError:
        abort(404)
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
    return render_template("slideshow.html", key=key)


@app.get("/show/<key>/playlist")
def slideshow_playlist(key: str):
    # Polled by the slideshow, so new photos and setting changes show up without a reload.
    cfg, frame = _web_frame(key)
    media = web_frame.list_media(registry.web_media_dir(cfg.data_dir, frame.id))
    return {"display": frames.display_for(cfg, frame), "items": media}


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
    return {"ok": True, "config_version": agent.agent_config(cfg, frame)["version"]}


def serve(host: str, port: int) -> None:
    from waitress import serve as waitress_serve

    app.secret_key = _secret_key()
    print(f"reframe web UI on http://{host}:{port}")
    waitress_serve(app, host=host, port=port, threads=4)
