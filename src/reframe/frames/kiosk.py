"""Turn a rooted Android frame (Cozyla) into a reframe kiosk, and back.

Provisioning installs Webview Kiosk (open source, AGPL-3.0, from F-Droid), points
it at the frame's slideshow, locks its settings with a generated password, makes
it the home screen and disables the Cozyla app. It also installs the reframe
agent (docs/agent.md), which manages the frame from then on. What the frame looked
like before is recorded, so deprovisioning puts it back exactly.
"""

import hashlib
import os
import posixpath
import secrets
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from ..config import Config
from .base import FrameError
from .cozyla_adb import CozylaAdbFrame

KIOSK_PKG = "uk.nktnet.webviewkiosk"
KIOSK_NAME = "Webview Kiosk"
KIOSK_VERSION = "0.26.28"
KIOSK_APK_URL = "https://f-droid.org/repo/uk.nktnet.webviewkiosk_143.apk"
KIOSK_APK_SHA256 = "65acc008255253d0dd0dbd9e0ed41b5af5416dd4cabd8bd77fc5d0a0816bc6e5"
KIOSK_PREFS_DIR = f"/data/data/{KIOSK_PKG}/shared_prefs"
KIOSK_PREFS = f"{KIOSK_PREFS_DIR}/user_settings.xml"

COZYLA_PKG = "com.cozyla.frame"

AGENT_PKG = "app.reframe.agent"
AGENT_VERSION = "0.4.0"
# The published release of the agent (agent/release.sh writes these). AGENT_APK
# overrides it with a local build (agent/build.sh).
AGENT_APK_URL = "https://github.com/ory-the-human/reframe/releases/download/agent-v0.4.0/reframe-agent-0.4.0.apk"
AGENT_APK_SHA256 = "8d75471d58befdd5e1d0678934ecf7e46c4d0ef8fe4bbc8b4dbeb850fbaf7c9b"
# Allwinner's BackgroundManagerService force-stops apps that aren't on this
# comma-separated list (a system setting) soon after the screen goes off.
KEEP_ALIVE_SETTING = "kill_background_services_list"

Progress = Callable[[str, int], None]  # (what's happening, percent done)


def _quiet(step: str, pct: int) -> None:
    pass

RESTRICTIONS = "/data/system/users/0/package-restrictions.xml"
TMP = "/data/local/tmp/reframe-kiosk.xml"


def _backup_file(cfg: Config, frame_id: str) -> Path:
    """The kiosk app's own settings, if it was installed before we provisioned."""
    return cfg.data_dir / "kiosk" / f"{frame_id}-user_settings.xml"


def kiosk_apk(cfg: Config) -> Path:
    """The pinned Webview Kiosk APK, downloaded once and checked against its hash."""
    path = cfg.data_dir / "cache" / f"webviewkiosk-{KIOSK_VERSION}.apk"
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == KIOSK_APK_SHA256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(KIOSK_APK_URL, timeout=120, headers={"User-Agent": "reframe"})
    resp.raise_for_status()
    digest = hashlib.sha256(resp.content).hexdigest()
    if digest != KIOSK_APK_SHA256:
        raise FrameError(f"{KIOSK_NAME} download doesn't match the expected checksum ({digest}); not installing it")
    path.write_bytes(resp.content)
    return path


def agent_apk(cfg: Config) -> Path:
    """The agent APK: AGENT_APK if set (a local build), else the pinned release."""
    local = os.environ.get("AGENT_APK")
    if local:
        if not Path(local).is_file():
            raise FrameError(f"AGENT_APK is set to {local}, which isn't a file")
        return Path(local)
    if not AGENT_APK_URL:
        raise FrameError("no reframe agent release is pinned yet; build one with agent/build.sh and set AGENT_APK to it")
    path = cfg.data_dir / "cache" / f"reframe-agent-{AGENT_VERSION}.apk"
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == AGENT_APK_SHA256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(AGENT_APK_URL, timeout=120, headers={"User-Agent": "reframe"})
    resp.raise_for_status()
    digest = hashlib.sha256(resp.content).hexdigest()
    if digest != AGENT_APK_SHA256:
        raise FrameError(f"reframe agent download doesn't match the expected checksum ({digest}); not installing it")
    path.write_bytes(resp.content)
    return path


def kiosk_settings(start_url: str, password: str) -> dict[str, Any]:
    return {
        "web_content.home_url": start_url,
        "appearance.address_bar_mode": "HIDDEN",
        "appearance.floating_toolbar_mode": "HIDDEN",
        "appearance.immersive_mode": "ALWAYS_ON",
        "appearance.show_loading_progress_bar": False,
        "device.keep_screen_on": True,
        # Opening the kiosk's settings on the frame asks for this password.
        "device.custom_auth_password": password,
        "device.settings_auth_requirement": "REQUIRE",
        "web_engine.media_playback_requires_user_gesture": False,
        "device.allow_camera": False,
        "device.allow_microphone": False,
        "device.allow_location": False,
        "web_lifecycle.refresh_on_loading_error_interval_seconds": 30,
        # Otherwise it reopens the last page it showed, not a changed home URL.
        "web_lifecycle.reset_on_launch": True,
    }


def set_prefs(xml: Optional[str], values: dict[str, Any]) -> str:
    """Set keys in an Android SharedPreferences file, keeping everything else."""
    root = ET.fromstring(xml) if xml else ET.Element("map")
    for key, value in values.items():
        for old in root.findall(f"*[@name='{key}']"):
            root.remove(old)
        if isinstance(value, bool):
            ET.SubElement(root, "boolean", name=key, value="true" if value else "false")
        elif isinstance(value, int):
            ET.SubElement(root, "int", name=key, value=str(value))
        else:
            ET.SubElement(root, "string", name=key).text = str(value)
    return "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n" + ET.tostring(root, encoding="unicode")


class Kiosk:
    def __init__(self, address: str):
        self.address = address
        self.adb = CozylaAdbFrame(address)

    def sh(self, command: str, timeout: int = 60) -> str:
        return self.adb.shell(command, timeout=timeout)

    def require_root(self) -> None:
        if self.sh("id -u").strip() != "0":
            raise FrameError("provisioning needs a root adb shell (Cozyla frames have one)")

    def installed(self, pkg: str) -> bool:
        return f"package:{pkg}" in self.sh(f"pm list packages {pkg}").split()

    def enabled(self, pkg: str) -> bool:
        return f"package:{pkg}" not in self.sh("pm list packages -d").split()

    def exists(self, remote: str) -> bool:
        return self.sh(f"[ -e {remote} ] && echo yes || echo no").strip() == "yes"

    def uid(self, pkg: str) -> str:
        for word in self.sh(f"dumpsys package {pkg}").split():
            if word.startswith("userId="):
                return word.split("=", 1)[1]
        raise FrameError(f"can't find {pkg}'s user id")

    def focused(self) -> str:
        for line in self.sh("dumpsys activity activities").splitlines():
            if "mFocusedActivity" in line:
                return line
        return ""

    def wait_focused(self, pkg: str, seconds: int = 30) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if pkg in self.focused():
                return True
            time.sleep(2)
        return False

    def wait_saved(self, pkg: str, disabled: bool, seconds: int = 30) -> None:
        """Android writes app on/off changes to disk about 10 s later; a reboot before
        that loses them. Wait until the change is on disk."""
        marker = f'"{pkg}" enabled="2"'
        deadline = time.time() + seconds
        while (marker in self.sh(f"cat {RESTRICTIONS}")) != disabled:
            if time.time() > deadline:
                raise FrameError(f"Android didn't save {pkg} as {'disabled' if disabled else 'enabled'}")
            time.sleep(2)

    def read_file(self, remote: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / posixpath.basename(remote)
            self.adb.run("pull", remote, str(local))
            return local.read_text()

    def write_app_file(self, remote: str, text: str, pkg: str) -> None:
        """Write a file into an app's data folder, owned by the app."""
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "file.xml"
            local.write_text(text)
            self.adb.run("push", str(local), TMP)
        uid = self.uid(pkg)
        self.sh(f"cp {TMP} {remote} && rm {TMP} && chown {uid}:{uid} {remote} && chmod 660 {remote} && restorecon {remote}")

    def version(self, pkg: str) -> str:
        for word in self.sh(f"dumpsys package {pkg}").split():
            if word.startswith("versionName="):
                return word.split("=", 1)[1]
        return ""

    def setting(self, key: str) -> Optional[str]:
        value = self.sh(f"settings get system {key}").strip()
        return None if value == "null" else value

    def set_setting(self, key: str, value: Optional[str]) -> None:
        if value is None:
            self.sh(f"settings delete system {key}")
        else:
            self.sh(f"settings put system {key} {value}")

    def set_clock(self) -> None:
        """Nothing sets the clock on a kiosk frame until the agent runs; it's 1970 after a reboot."""
        self.sh("date -u " + time.strftime("%m%d%H%M%Y.%S", time.gmtime()))

    def start_agent(self, server: str, key: str) -> None:
        extras = f"--es server {server} --es key {key}"
        out = self.sh(f"am startservice -n {AGENT_PKG}/.AgentService {extras} 2>&1")
        if "Error" in out:
            # The background manager refuses services of apps that haven't shown an activity.
            self.sh(f"am start -n {AGENT_PKG}/.StartActivity {extras}")

    def running(self, pkg: str) -> bool:
        return pkg in self.sh("ps").split()

    def home(self) -> None:
        self.sh("am start -a android.intent.action.MAIN -c android.intent.category.HOME")


def provision(
    cfg: Config,
    frame_id: str,
    address: str,
    server: str,
    slideshow_key: str,
    agent_key: str,
    state: dict[str, Any],
    save: Callable[[dict[str, Any]], None],
    progress: Progress = _quiet,
) -> dict[str, Any]:
    """Make the frame a reframe kiosk and return its new kiosk record.

    `server` is this server's address as the frame reaches it. `state` is the
    frame's current record (empty the first time); running it again updates the
    settings and keeps the original state recorded the first time. `save` is
    called with that record before anything on the frame changes, so a failure
    halfway can still be undone."""
    start_url = f"{server}/show/{slideshow_key}"
    progress("Connecting to the frame", 5)
    k = Kiosk(address)
    try:
        k.require_root()

        original: Optional[dict[str, Any]] = state.get("original")
        if original is None:
            progress("Recording how the frame is set up", 12)
            cozyla = k.installed(COZYLA_PKG)
            original = {
                "cozyla_installed": cozyla,
                "cozyla_enabled": cozyla and k.enabled(COZYLA_PKG),
                "kiosk_installed": k.installed(KIOSK_PKG),
            }
            if original["kiosk_installed"] and k.exists(KIOSK_PREFS):
                backup = _backup_file(cfg, frame_id)
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(k.read_file(KIOSK_PREFS))
        if "agent_installed" not in original:
            # Also for kiosks set up before the agent existed.
            original = {**original, "agent_installed": k.installed(AGENT_PKG), "keep_alive": k.setting(KEEP_ALIVE_SETTING)}
        password = state.get("password") or secrets.token_urlsafe(12)
        save({**state, "original": original, "password": password})

        if not k.installed(KIOSK_PKG):
            progress(f"Downloading {KIOSK_NAME}", 20)
            apk = kiosk_apk(cfg)
            progress(f"Installing {KIOSK_NAME} on the frame", 35)
            k.adb.run("install", str(apk), timeout=300)

        # The app creates its settings folder on first start; edit its settings while it's stopped.
        if not k.exists(KIOSK_PREFS_DIR):
            progress(f"Starting {KIOSK_NAME} for the first time", 55)
            k.sh(f"am start -n {KIOSK_PKG}/.MainActivity")
            deadline = time.time() + 45
            while not k.exists(KIOSK_PREFS_DIR):
                if time.time() > deadline:
                    raise FrameError(f"{KIOSK_NAME} didn't create its settings folder after starting")
                time.sleep(2)
            time.sleep(3)
        progress("Writing the kiosk settings", 68)
        k.sh(f"am force-stop {KIOSK_PKG}")
        current = k.read_file(KIOSK_PREFS) if k.exists(KIOSK_PREFS) else None
        k.write_app_file(KIOSK_PREFS, set_prefs(current, kiosk_settings(start_url, password)), KIOSK_PKG)

        if k.version(AGENT_PKG) != AGENT_VERSION:
            progress("Installing the reframe agent", 70)
            k.adb.run("install", "-r", str(agent_apk(cfg)), timeout=300)
        progress("Starting the reframe agent", 73)
        keep = [p for p in (original["keep_alive"] or "").split(",") if p]
        keep += [p for p in (AGENT_PKG, KIOSK_PKG) if p not in keep]
        k.set_setting(KEEP_ALIVE_SETTING, ",".join(keep))
        k.set_clock()
        k.start_agent(server, agent_key)

        # The kiosk is a home screen as soon as it's installed; turning Cozyla off leaves it the only one.
        if original["cozyla_installed"] and k.enabled(COZYLA_PKG):
            progress("Turning off the Cozyla app", 76)
            k.sh(f"pm disable {COZYLA_PKG}")
            progress("Waiting for Android to save the change", 82)
            k.wait_saved(COZYLA_PKG, disabled=True)
        progress("Opening the slideshow", 94)
        k.home()
        if not k.wait_focused(KIOSK_PKG):
            raise FrameError(f"{KIOSK_NAME} didn't come to the front; the screen shows: {k.focused().strip()}")
        if not k.running(AGENT_PKG):
            raise FrameError("the reframe agent didn't start")

        return {
            "original": original,
            "password": password,
            "app": KIOSK_NAME,
            "app_version": KIOSK_VERSION,
            "agent_version": AGENT_VERSION,
            "start_url": start_url,
            "provisioned_at": int(time.time()),
        }
    finally:
        k.adb.close()


def deprovision(cfg: Config, frame_id: str, address: str, state: dict[str, Any], progress: Progress = _quiet) -> None:
    """Put the frame back the way provision() found it."""
    original = state.get("original")
    if not original:
        raise FrameError("this frame has no record of its original state, so it can't be restored automatically")
    progress("Connecting to the frame", 5)
    k = Kiosk(address)
    try:
        k.require_root()

        # The agent goes first, so its watchdog doesn't turn the Cozyla app off again.
        if "agent_installed" in original:
            if not original["agent_installed"] and k.installed(AGENT_PKG):
                progress("Removing the reframe agent", 12)
                k.adb.run("uninstall", AGENT_PKG, timeout=120)
            k.set_setting(KEEP_ALIVE_SETTING, original["keep_alive"])

        # Cozyla becomes a home screen again before the kiosk stops being one.
        if original["cozyla_installed"] and original["cozyla_enabled"]:
            progress("Turning the Cozyla app back on", 20)
            k.sh(f"pm enable {COZYLA_PKG}")
            progress("Waiting for Android to save the change", 35)
            k.wait_saved(COZYLA_PKG, disabled=False)
        if original["kiosk_installed"]:
            progress(f"Restoring {KIOSK_NAME}'s own settings", 60)
            k.sh(f"am force-stop {KIOSK_PKG}")
            backup = _backup_file(cfg, frame_id)
            if backup.exists():
                k.write_app_file(KIOSK_PREFS, backup.read_text(), KIOSK_PKG)
            else:
                k.sh(f"rm -f {KIOSK_PREFS}")
        elif k.installed(KIOSK_PKG):
            progress(f"Removing {KIOSK_NAME}", 60)
            k.adb.run("uninstall", KIOSK_PKG, timeout=120)
        # Home only once a single home screen is left, or Android asks which one to use.
        progress("Opening the Cozyla app", 85)
        k.home()
        if original["cozyla_installed"] and original["cozyla_enabled"] and not k.wait_focused(COZYLA_PKG):
            raise FrameError(f"the Cozyla app didn't come back to the front; the screen shows: {k.focused().strip()}")
        _backup_file(cfg, frame_id).unlink(missing_ok=True)
    finally:
        k.adb.close()
