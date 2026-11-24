import argparse
import collections
import json
import sys
import time
from pathlib import Path
from typing import Optional

import qrcode

from . import config, frames, snapshots
from .frames import registry
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
    all_frames = frames.load_all(cfg)
    if not all_frames:
        print("No frames yet; add one with `cozysync frames add NAME --type ...`.")
    for f in all_frames:
        albums = ", ".join(a["name"] for a in f.albums) or "all albums"
        print(f"{f.id}  {f.name:<20} {f.kind:<11} {f.target:<28} {albums}")


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

    p = sub.add_parser("frames", help="list, add or remove frames")
    fsub = p.add_subparsers(dest="action")
    fp = fsub.add_parser("add", help="add a frame")
    fp.add_argument("name")
    fp.add_argument("--type", required=True, choices=list(registry.KINDS))
    fp.add_argument("--address", help="cozyla_adb: host:port")
    fp.add_argument("--folder", help="folder: path inside the container")
    fp.add_argument("--album", action="append", metavar="ID", help="album id to show (repeat); default all")
    fp = fsub.add_parser("remove", help="remove a frame")
    fp.add_argument("name", help="frame name or id")
    p.set_defaults(func=cmd_frames)

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
