"""Cozyla frame (or any rooted Android frame) over adb on Wi-Fi.

Files go to a folder on /sdcard. Making them show up inside the Cozyla app
(ItemEntry rows) is PLAN.md phase 3.
"""

import posixpath
import subprocess
from pathlib import Path

from .base import FrameError

DEFAULT_DIR = "/sdcard/Android/obb/com.cozyla.frame/media/IMAGE/cozysync"


def adb(address: str, *args: str, timeout: int = 60) -> str:
    result = subprocess.run(
        ["adb", "-s", address, *args], capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise FrameError(f"adb {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def connect(address: str) -> None:
    # `adb connect` exits 0 even when it fails, so check the output instead.
    out = subprocess.run(
        ["adb", "connect", address], capture_output=True, text=True, timeout=30
    ).stdout
    if "connected to" not in out:
        raise FrameError(
            f"could not connect to {address}: {out.strip()} "
            "(on first connect, tap 'Allow' on the frame's debugging prompt and retry)"
        )


class CozylaAdbFrame:
    kind = "cozyla_adb"
    max_px = 1280  # 800x1280 panel

    def __init__(self, address: str, directory: str = DEFAULT_DIR):
        self.address = address
        self.directory = directory
        self._connected = False

    def run(self, *args: str, timeout: int = 60) -> str:
        if not self._connected:
            connect(self.address)
            self._connected = True
        return adb(self.address, *args, timeout=timeout)

    def shell(self, command: str, timeout: int = 60) -> str:
        """Run a shell command and raise if it fails.

        Old adbd (Android 6) doesn't report exit codes, so the command echoes its own.
        """
        out = self.run("shell", f"{command}; echo __rc=$?", timeout=timeout).replace("\r", "")
        body, _, rc = out.rstrip("\n").rpartition("__rc=")
        if rc.strip() != "0":
            raise FrameError(f"`{command}` failed on {self.address}: {body.strip() or 'exit ' + rc.strip()}")
        return body

    def check(self) -> dict[str, str]:
        props = {
            "model": "ro.product.model",
            "android": "ro.build.version.release",
            "build": "ro.build.display.id",
        }
        info = {k: self.run("shell", "getprop", p).strip() for k, p in props.items()}
        info["whoami"] = self.run("shell", "id", "-un").strip()
        info["screen"] = self.run("shell", "wm", "size").strip()
        info["directory"] = self.directory
        return info

    def put(self, local: Path, name: str) -> str:
        remote = posixpath.join(self.directory, name)
        self.run("shell", "mkdir", "-p", self.directory)
        self.run("push", str(local), remote, timeout=300)
        return remote

    def remove(self, frame_path: str) -> None:
        if not frame_path.startswith(self.directory + "/"):
            raise FrameError(f"refusing to delete {frame_path}: outside {self.directory}")
        self.run("shell", "rm", "-f", frame_path)
