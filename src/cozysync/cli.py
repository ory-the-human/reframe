import argparse
import collections
import json
import os
import secrets
import shlex
import sys
import time
from pathlib import Path
from typing import Optional

import qrcode

from . import agent, config, frames, snapshots, sources
from .frames import kiosk, registry
from .sources import get_source
from .sources.google_ambient.api import AmbientClient
from .sources.google_ambient.auth import TokenStore
from .sources.google_ambient.login import LoginFlow
from .store import read_json


def print_qr(data: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.print_ascii(invert=True)


def print_sources(device: dict) -> None:
    sources = device.get("mediaSources", [])
    print(f"\nSelected albums ({len(sources)}):")
    for s in sources:
        print(f"  {s.get('id')}  {s.get('displayName')}")


def load_device(cfg: config.Config) -> dict:
    device = read_json(cfg.device_file)
    if device is None:
        sys.exit("No Ambient device yet; run `cozysync login` first.")
    return device


def cmd_login(cfg: config.Config, args: argparse.Namespace) -> None:
    if not cfg.has_client:
        sys.exit("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set (see .env.example)")

    def on_update(flow: LoginFlow) -> None:
        if flow.step == "awaiting_signin":
            print("\nSign in to Google to authorize the frame:")
            print(f"  1. Open {flow.verification_url}")
            print(f"  2. Enter code: {flow.user_code}\n")
            print_qr(flow.verification_url)
            print("Waiting for sign-in...")
        elif flow.step == "registering":
            print("Signed in. Registering the Ambient device...")
        elif flow.step == "awaiting_albums":
            print("\nPick the albums to show, in Google Photos:")
            print(f"  {flow.settings_uri}\n")
            print_qr(flow.settings_uri)
            print("Waiting for album selection...")

    flow = LoginFlow(cfg, new_device=args.new_device, on_update=on_update)
    flow.run()
    if flow.step == "error":
        sys.exit(f"Login failed: {flow.error}")
    print_sources(flow.device)


def cmd_device(cfg: config.Config, args: argparse.Namespace) -> None:
    tokens = TokenStore(cfg)
    device = AmbientClient(tokens).get_device(load_device(cfg)["id"])
    print(json.dumps(device, indent=2))
    print_sources(device)

    token = read_json(cfg.token_file) or {}
    if "first_obtained_at" in token:
        age_days = (time.time() - token["first_obtained_at"]) / 86400
        print(f"\nRefresh token age: {age_days:.1f} days (Testing-mode apps expire at 7)")


def cmd_list(cfg: config.Config, args: argparse.Namespace) -> None:
    source = get_source(cfg)
    albums = source.albums()
    if args.album:
        albums = [a for a in albums if args.album in (a.id, a.name)]
        if not albums:
            sys.exit(f"No selected album matches {args.album!r}.")

    path, _, listings = snapshots.take(cfg, source, albums)
    for listing in listings:
        items = listing.items
        mimes = collections.Counter(i.mime_type for i in items)
        partial = "" if listing.complete else " (INCOMPLETE: removals can't be trusted)"
        print(f"\n== {listing.album.name} ({listing.album.id}): {len(items)} items{partial}")
        print("   mime types: " + ", ".join(f"{m}={n}" for m, n in mimes.most_common()))
        for item in items[: args.show]:
            print(f"   {item.id[:24]}…  {item.created}  {item.mime_type}  {item.width}x{item.height}")
        if args.download:
            max_px = frames.largest_max_px(cfg)
            for r in snapshots.download_samples(source, items[: args.download], cfg.data_dir / "samples", max_px):
                if "file" in r:
                    print(f"   downloaded {r['file']} ({r['kb']} KB)")
                else:
                    print(f"   {r['id'][:24]}… download failed: {r['error']}")

    print(f"\nSaved snapshot to {path}")


def cmd_diff(cfg: config.Config, args: argparse.Namespace) -> None:
    """Compare two snapshots to see which items were added or removed per album."""
    if args.old and args.new:
        old_path, new_path = Path(args.old), Path(args.new)
        changes = snapshots.diff(read_json(old_path), read_json(new_path))
    else:
        latest = snapshots.diff_latest(cfg)
        if latest is None:
            sys.exit("Need two snapshots from this version; run `cozysync list` twice.")
        old_path, new_path, changes = latest

    print(f"old: {old_path.name}")
    print(f"new: {new_path.name}")
    for c in changes:
        if "only_in" in c:
            print(f"\n== {c['album']}: only in {c['only_in']} snapshot")
            continue
        print(
            f"\n== {c['album']}: {c['old_count']} -> {c['new_count']} items,"
            f" +{len(c['added'])} -{len(c['removed'])}"
        )
        if not c["reliable"]:
            print("   warning: a listing was incomplete, so 'removed' is unreliable")
        for media_id in c["added"]:
            print(f"   + {media_id}")
        for media_id in c["removed"]:
            print(f"   - {media_id}")


def cmd_frames(cfg: config.Config, args: argparse.Namespace) -> None:
    if args.action == "add":
        fields = {"address": args.address or "", "folder": args.folder or ""}
        fields["albums"] = [{"id": a, "name": a} for a in args.album or []]
        frame = registry.add(cfg, args.name, args.type, **fields)
        print(f"Added {frame.name} ({frame.id}).")
        return
    if args.action == "remove":
        frame = registry.find(frames.load_all(cfg), args.name)
        registry.remove(cfg, frame.id)
        print(f"Removed {frame.name} ({frame.id}).")
        return
    if args.action == "edit":
        _edit_frame(cfg, registry.find(frames.load_all(cfg), args.name), args)
        return
    if args.action == "show":
        _show_frame(cfg, registry.find(frames.load_all(cfg), args.name))
        return
    all_frames = frames.load_all(cfg)
    if not all_frames:
        print("No frames yet; add one with `cozysync frames add NAME --type ...`.")
    for f in all_frames:
        albums = ", ".join(a["name"] for a in f.albums) or "all albums"
        kind = f"{f.kind} (kiosk)" if f.is_kiosk else f.kind
        print(f"{f.id}  {f.name:<20} {kind:<19} {f.target:<28} {albums}")


DISPLAY_FLAGS = {  # CLI flag -> display setting
    "interval": "display_interval",
    "layout": "display_layout",
    "orientation": "display_orientation",
    "shuffle": "display_shuffle",
    "fill": "display_fill",
}


SCREEN_FLAGS = {  # CLI flag -> screen setting
    "schedule": "screen_schedule",
    "dark": "screen_dark",
    "dark_lux": "screen_dark_lux",
    "dark_seconds": "screen_dark_seconds",
}


def _check_screen(key: str, value: str) -> None:
    error = config.check_screen(key, value)
    if error:
        sys.exit(error)


def _check_display(key: str, value: str) -> None:
    """Reject display values the slideshow wouldn't understand ("" means the default)."""
    if not value:
        return
    if key == "display_interval":
        if not value.isdigit() or not 3 <= int(value) <= 3600:
            sys.exit("interval: give seconds per photo, 3 to 3600.")
    elif value not in config.DISPLAY_CHOICES[key]:
        sys.exit(f"{key[len('display_'):]}: choose one of {', '.join(config.DISPLAY_CHOICES[key])}.")


def _edit_frame(cfg: config.Config, f: registry.FrameConfig, args: argparse.Namespace) -> None:
    for field in ("address", "folder"):
        if getattr(args, field) is not None:
            setattr(f, field, getattr(args, field))
    if args.rename:
        f.name = args.rename
    if args.all_albums:
        f.albums = []
    elif args.album:
        known = {a["id"]: a["name"] for a in f.albums}
        f.albums = [{"id": a, "name": known.get(a, a)} for a in args.album]
    screen = {flag: key for flag, key in SCREEN_FLAGS.items() if getattr(args, flag) is not None}
    if screen and not f.is_kiosk:
        sys.exit("Screen settings are for kiosk frames; set one up with `cozysync kiosk setup`.")
    for flag, key in {**DISPLAY_FLAGS, **screen}.items():
        value = getattr(args, flag)
        if value is None:
            continue
        (_check_screen if key in config.SCREEN_KEYS else _check_display)(key, value)
        if value:
            f.display[key] = value
        else:
            f.display.pop(key, None)
    registry.update(cfg, f)
    print(f"Saved {f.name}.")
    _show_frame(cfg, f)


def _slideshow_link(cfg: config.Config, f: registry.FrameConfig) -> str:
    if cfg.public_url:
        return f"{cfg.public_url.rstrip('/')}/show/{f.key}"
    return f"/show/{f.key} on this server (set public_url for the full link)"


def _show_frame(cfg: config.Config, f: registry.FrameConfig) -> None:
    print(f"{'id':>12}: {f.id}")
    print(f"{'name':>12}: {f.name}")
    print(f"{'type':>12}: {registry.KINDS.get(f.kind, f.kind)}")
    print(f"{'target':>12}: {f.target}")
    print(f"{'albums':>12}: {', '.join(a['name'] for a in f.albums) or 'all'}")
    if f.shows_slideshow:
        effective = frames.display_for(cfg, f)
        for flag, key in DISPLAY_FLAGS.items():
            own = " (this frame)" if f.display.get(key) else ""
            value = effective[flag]
            print(f"{flag:>12}: {('yes' if value else 'no') if isinstance(value, bool) else value}{own}")
        print(f"{'slideshow':>12}: {_slideshow_link(cfg, f)}")
    if f.is_kiosk:
        screen = frames.screen_for(cfg, f)
        schedule = screen["schedule"]
        own = {flag: " (this frame)" if f.display.get(key) else "" for flag, key in SCREEN_FLAGS.items()}
        print(f"{'schedule':>12}: {'off ' + schedule['off'] + ' to ' + schedule['on'] if schedule else 'always on'}{own['schedule']}")
        dark = f"off below {screen['dark_lux']} lux for {screen['dark_seconds']} s" if screen["dark"] else "ignored"
        print(f"{'room light':>12}: {dark}{own['dark'] or own['dark_lux'] or own['dark_seconds']}")
        print(f"{'kiosk':>12}: {f.kiosk.get('app')} {f.kiosk.get('app_version')}, plays {f.kiosk.get('start_url')}")
        print(f"{'':>12}  password: `cozysync kiosk show {shlex.quote(f.name)}`")
    elif f.kiosk:
        print(f"{'kiosk':>12}: setup didn't finish; run `cozysync kiosk setup` or `restore`")
    if f.agent_key:
        print(f"{'agent':>12}: {_health_line(agent.health(cfg, f))}")


def _health_line(h: Optional[dict]) -> str:
    if not h:
        return "no report yet"
    parts = ["online" if h["online"] else f"last heard {agent.duration(h['age'])} ago"]
    if h["temp"] is not None:
        parts.append(f"{h['temp']:.0f} °C")
    if h["load"]:
        parts.append(f"load {h['load'][0]:.2f}")
    if h["mem_pct"] is not None:
        parts.append(f"memory {h['mem_pct']:.0f}%")
    if h["screen"]:
        parts.append("screen " + ("on" if h["screen"].get("on") else "off"))
    return ", ".join(parts)


def cmd_health(cfg: config.Config, args: argparse.Namespace) -> None:
    """What each frame's agent last reported."""
    shown = [f for f in _selected(cfg, args.frame) if f.agent_key]
    if not shown:
        sys.exit("No frame has an agent yet.")
    for f in shown:
        h = agent.health(cfg, f)
        print(f"\n== {f.name} ({f.id}): {_health_line(h)}")
        if not h:
            continue
        rows = {
            "temps": ", ".join(f"{k} {v:.1f} °C" for k, v in h["temperatures"].items()),
            "load": " / ".join(f"{x:.2f}" for x in h["load"] or []) + (f" ({h['cpus']} cores)" if h["cpus"] else ""),
            "memory": f"{h['mem_pct']:.0f}% of {h['mem_total_mb']:.0f} MB" if h["mem_pct"] is not None else "",
            "storage": f"{h['storage_free_mb']:.0f} MB free of {h['storage_total_mb']:.0f} MB" if h["storage_total_mb"] else "",
            "uptime": agent.duration(h["uptime"]) if h["uptime"] is not None else "",
            "screen": json.dumps(h["screen"]) if h["screen"] else "",
            "on screen": h["foreground"] or "",
            "clock": f"{h['time']} ({h['timezone']})" if h["time"] else "",
            "agent": (h["agent_version"] or "?") + ("" if h["config_current"] else ", hasn't picked up the latest settings"),
        }
        for key, value in rows.items():
            if value:
                print(f"{key:>10}: {value}")
        for fix in h["fixes"]:
            print(f"{'fixed':>10}: {agent.clock(fix['at'])} {fix['what']}")


def cmd_agent(cfg: config.Config, args: argparse.Namespace) -> None:
    """The agent's key and what the server sends it, for setting up or testing an agent by hand."""
    f = registry.find(frames.load_all(cfg), args.frame)
    if args.new_key:
        f.agent_key = ""
    key = agent.ensure_key(cfg, f)
    base = cfg.public_url.rstrip("/") or "http://<this server>"
    print(f"Config: {base}/agent/v1/config")
    print(f"Report: {base}/agent/v1/report")
    print(f"Key:    {key}  (send as 'Authorization: Bearer <key>')")
    print(json.dumps(agent.agent_config(cfg, f), indent=2))


def _selected(cfg: config.Config, ref: Optional[str]) -> list[registry.FrameConfig]:
    all_frames = frames.load_all(cfg)
    return [registry.find(all_frames, ref)] if ref else all_frames


def cmd_frame_check(cfg: config.Config, args: argparse.Namespace) -> None:
    for f in _selected(cfg, args.frame):
        print(f"\n== {f.name} ({f.id}, {f.kind})")
        try:
            for key, value in frames.build(cfg, f).check().items():
                print(f"{key:>10}: {value}")
        except Exception as e:
            print(f"     error: {e}")


def cmd_frame_put(cfg: config.Config, args: argparse.Namespace) -> None:
    """Copy local files to a frame, for testing before the sync engine exists."""
    f = registry.find(frames.load_all(cfg), args.frame)
    adapter = frames.build(cfg, f)
    for name in args.files:
        path = Path(name)
        print(f"{path.name} -> {adapter.put(path, path.name)}")


def _print_progress(step: str, pct: int) -> None:
    print(f"  [{pct:3d}%] {step}")


def cmd_kiosk(cfg: config.Config, args: argparse.Namespace) -> None:
    f = registry.find(frames.load_all(cfg), args.frame)
    if f.kind != "cozyla_adb":
        sys.exit(f"{f.name} isn't a Cozyla frame over adb.")

    def save(state: dict) -> None:
        f.kiosk = state
        registry.update(cfg, f)

    if args.action == "setup":
        base = (args.url or cfg.public_url).rstrip("/")
        if not base:
            sys.exit("Give the server's address as frames see it: --url http://192.168.1.10:1992 (or set PUBLIC_URL).")
        if not f.key:
            f.key = secrets.token_urlsafe(18)
        print(f"Setting up {f.name} as a kiosk (about a minute)...")
        save(kiosk.provision(cfg, f.id, f.address, f"{base}/show/{f.key}", f.kiosk, save, _print_progress))
        print(f"Done. The frame plays {f.kiosk['start_url']}")
    elif args.action == "restore":
        print(f"Restoring {f.name} to how it was...")
        kiosk.deprovision(cfg, f.id, f.address, f.kiosk, _print_progress)
        save({})
        print("Done.")
    if args.action in ("setup", "show"):
        if f.kiosk:
            print(f"Kiosk settings password: {f.kiosk.get('password')}")
            print("(On the frame: tap the top-left quarter of the screen 10 times quickly.)")
        else:
            print(f"{f.name} isn't set up as a kiosk.")


SECRET_SETTINGS = {"client_secret"}


def cmd_settings(cfg: config.Config, args: argparse.Namespace) -> None:
    saved = read_json(config.settings_file()) or {}
    if args.action in ("set", "unset"):
        key = args.key
        if key not in config.SETTINGS:
            sys.exit(f"Unknown setting {key!r}; settings: {', '.join(config.SETTINGS)}.")
        value = args.value if args.action == "set" else ""
        if key in config.DISPLAY_KEYS:
            _check_display(key, value)
        elif key in config.SCREEN_KEYS:
            _check_screen(key, value)
        elif key == "source" and value and value not in sources.KINDS:
            sys.exit(f"source: choose one of {', '.join(sources.KINDS)}.")
        config.save_settings({key: value})
        print(f"{'Set' if value else 'Cleared'} {key}.")
        return
    for key, (env, default) in config.SETTINGS.items():
        value = getattr(cfg, key)
        origin = "saved" if saved.get(key) else ("env " + env if os.environ.get(env) else "default")
        if key in SECRET_SETTINGS and value:
            value = "(set)"
        print(f"{key:<20} {value or '-':<40} {origin}")


def cmd_signout(cfg: config.Config, args: argparse.Namespace) -> None:
    if args.forget_device:
        saved = read_json(cfg.device_file)
        if saved:
            AmbientClient(TokenStore(cfg)).delete_device(saved["id"])
            cfg.device_file.unlink()
            print("Removed the device from Google Photos.")
    cfg.token_file.unlink(missing_ok=True)
    print("Signed out.")


def cmd_web(cfg: config.Config, args: argparse.Namespace) -> None:
    from .web import serve

    serve(args.host, args.port)


def main() -> None:
    parser = argparse.ArgumentParser(prog="cozysync", description="Sync photo albums to photo frames")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("web", help="run the web UI for setup, sign-in and testing")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=1992)
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("login", help="Google Photos Ambient: sign in and register the device")
    p.add_argument("--new-device", action="store_true", help="register a fresh device even if one exists")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("device", help="Google Photos Ambient: show the device and its albums")
    p.set_defaults(func=cmd_device)

    p = sub.add_parser("list", help="list every item in the selected albums and save a snapshot")
    p.add_argument("--album", help="only this album (id or name)")
    p.add_argument("--show", type=int, default=10, help="items to print per album")
    p.add_argument("--download", type=int, default=0, metavar="N", help="download N samples per album")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("diff", help="compare two list snapshots (latest two by default)")
    p.add_argument("old", nargs="?", help="older snapshot file")
    p.add_argument("new", nargs="?", help="newer snapshot file")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("frames", help="list, add, show, edit or remove frames")
    fsub = p.add_subparsers(dest="action")
    fp = fsub.add_parser("add", help="add a frame")
    fp.add_argument("name")
    fp.add_argument("--type", required=True, choices=list(registry.KINDS))
    fp.add_argument("--address", help="cozyla_adb: host:port")
    fp.add_argument("--folder", help="folder: path inside the container")
    fp.add_argument("--album", action="append", metavar="ID", help="album id to show (repeat); default all")
    fp = fsub.add_parser("remove", help="remove a frame")
    fp.add_argument("name", help="frame name or id")
    fp = fsub.add_parser("show", help="a frame's details, slideshow link and kiosk state")
    fp.add_argument("name", help="frame name or id")
    fp = fsub.add_parser("edit", help="change a frame; display flags take \"\" to go back to the default")
    fp.add_argument("name", help="frame name or id")
    fp.add_argument("--rename", metavar="NAME", help="new name")
    fp.add_argument("--address", help="cozyla_adb: host:port")
    fp.add_argument("--folder", help="folder: path inside the container")
    albums = fp.add_mutually_exclusive_group()
    albums.add_argument("--album", action="append", metavar="ID", help="album id to show (repeat); replaces the list")
    albums.add_argument("--all-albums", action="store_true", help="show every album")
    fp.add_argument("--interval", metavar="SECONDS", help="seconds per photo")
    for flag, key in DISPLAY_FLAGS.items():
        if flag != "interval":
            fp.add_argument(f"--{flag}", metavar="|".join(config.DISPLAY_CHOICES[key]))
    fp.add_argument("--schedule", metavar="always|HH:MM-HH:MM", help="kiosk: hours the screen is off, e.g. 23:00-07:00")
    fp.add_argument("--dark", metavar="yes|no", help="kiosk: turn the screen off when the room is dark")
    fp.add_argument("--dark-lux", metavar="LUX", help="kiosk: below this the room counts as dark")
    fp.add_argument("--dark-seconds", metavar="SECONDS", help="kiosk: how long it must be dark first")
    p.set_defaults(func=cmd_frames)

    p = sub.add_parser("settings", help="list settings, or set or clear one (same as the web UI's Settings)")
    ssub = p.add_subparsers(dest="action")
    sp = ssub.add_parser("set", help="save a setting")
    sp.add_argument("key")
    sp.add_argument("value")
    sp = ssub.add_parser("unset", help="clear a saved setting (back to .env or the default)")
    sp.add_argument("key")
    p.set_defaults(func=cmd_settings)

    p = sub.add_parser("signout", help="google_ambient: forget the sign-in")
    p.add_argument("--forget-device", action="store_true", help="also remove the device from Google Photos")
    p.set_defaults(func=cmd_signout)

    p = sub.add_parser("kiosk", help="set up a Cozyla frame as a cozysync kiosk, or restore it")
    p.add_argument("action", choices=["setup", "restore", "show"])
    p.add_argument("frame", help="frame name or id")
    p.add_argument("--url", help="setup: this server's address as frames see it, e.g. http://192.168.1.10:1992")
    p.set_defaults(func=cmd_kiosk)

    p = sub.add_parser("health", help="what the agents on kiosk frames last reported")
    p.add_argument("frame", nargs="?", help="frame name or id; default all")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("agent", help="a frame's agent key and config, for testing an agent")
    p.add_argument("frame", help="frame name or id")
    p.add_argument("--new-key", action="store_true", help="replace the key (the old one stops working)")
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser("frame-check", help="connect to frames and print their details")
    p.add_argument("frame", nargs="?", help="frame name or id; default all")
    p.set_defaults(func=cmd_frame_check)

    p = sub.add_parser("frame-put", help="copy local files to a frame (for testing)")
    p.add_argument("frame", help="frame name or id")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_frame_put)

    args = parser.parse_args()
    try:
        args.func(config.load(), args)
    except frames.FrameError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
