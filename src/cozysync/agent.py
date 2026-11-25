"""The server side of the agent app that runs on kiosk frames.

The agent calls the server, never the other way round, so nothing depends on the
frame's adb: it fetches its config and sends health reports, with the frame's agent
key as a bearer token (docs/agent.md). Reports are kept per frame in
/data/agents/<frame id>.json: the latest in full, a short history for charts, and
the watchdog's recent fixes.
"""

import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from .config import Config
from .frames import registry
from .frames.kiosk import COZYLA_PKG, KIOSK_PKG, kiosk_settings
from .store import read_json, write_json

API_VERSION = 1
REPORT_SECONDS = 60
STALE_SECONDS = 3 * REPORT_SECONDS
MAX_REPORT_BYTES = 64 * 1024
HISTORY = 720  # samples: 12 hours at one a minute
FIXES = 50


def _file(cfg: Config, frame_id: str) -> Path:
    return cfg.data_dir / "agents" / f"{frame_id}.json"


def ensure_key(cfg: Config, frame: registry.FrameConfig) -> str:
    if not frame.agent_key:
        frame.agent_key = secrets.token_urlsafe(24)
        registry.update(cfg, frame)
    return frame.agent_key


def agent_config(cfg: Config, frame: registry.FrameConfig) -> dict[str, Any]:
    """What the agent on this frame should do. `version` changes whenever the rest does."""
    body: dict[str, Any] = {
        "api": API_VERSION,
        "frame": {"id": frame.id, "name": frame.name},
        "screen": registry.screen_for(cfg, frame),
        "report_seconds": REPORT_SECONDS,
        "kiosk": None,
        "keep_disabled": [],
    }
    if frame.is_kiosk:
        settings = kiosk_settings(frame.kiosk["start_url"], "")
        # The agent checks the kiosk's settings file, which it can read anyway; the
        # password isn't needed for that.
        settings.pop("device.custom_auth_password")
        body["kiosk"] = {"package": KIOSK_PKG, "home_url": frame.kiosk["start_url"], "settings": settings}
        if frame.kiosk.get("original", {}).get("cozyla_installed"):
            body["keep_disabled"] = [COZYLA_PKG]
    body["version"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    return body


def load(cfg: Config, frame_id: str) -> Optional[dict[str, Any]]:
    return read_json(_file(cfg, frame_id))


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _sample(report: dict[str, Any], at: float) -> dict[str, Any]:
    """The few numbers kept per report for the history charts."""
    temps = [t for t in map(_num, (report.get("temperatures") or {}).values()) if t is not None]
    load = report.get("load")
    memory = report.get("memory") or {}
    total, available = _num(memory.get("total_kb")), _num(memory.get("available_kb"))
    return {
        "t": int(at),
        "temp": max(temps) if temps else None,
        "load": _num(load[0]) if isinstance(load, list) and load else None,
        "mem": round(100 * (1 - available / total), 1) if total and available is not None else None,
        "on": (report.get("screen") or {}).get("on"),
    }


def save_report(cfg: Config, frame: registry.FrameConfig, report: dict[str, Any]) -> None:
    now = time.time()
    state = load(cfg, frame.id) or {}
    fixes = state.get("fixes", [])
    for fix in report.get("fixes") or []:
        if isinstance(fix, dict):
            at = _num(fix.get("at"))
            fixes.append({"at": int(at) if at else int(now), "what": str(fix.get("what", ""))[:200]})
    _file(cfg, frame.id).parent.mkdir(parents=True, exist_ok=True)
    write_json(
        _file(cfg, frame.id),
        {
            "received_at": int(now),
            "latest": report,
            "history": (state.get("history", []) + [_sample(report, now)])[-HISTORY:],
            "fixes": fixes[-FIXES:],
        },
    )


def health(cfg: Config, frame: registry.FrameConfig) -> Optional[dict[str, Any]]:
    """The frame's last report, flattened for the admin pages. None if it never reported."""
    state = load(cfg, frame.id)
    if not state:
        return None
    report = state["latest"]
    age = time.time() - state["received_at"]
    memory = report.get("memory") or {}
    storage = report.get("storage") or {}
    temps = {str(k): v for k, v in (report.get("temperatures") or {}).items() if _num(v) is not None}
    sample = state["history"][-1] if state.get("history") else {}
    return {
        "online": age < STALE_SECONDS,
        "age": age,
        "agent_version": report.get("agent_version"),
        "config_current": report.get("config_version") == agent_config(cfg, frame)["version"],
        "temperatures": temps,
        "temp": sample.get("temp"),
        "load": report.get("load") if isinstance(report.get("load"), list) else None,
        "cpus": report.get("cpus"),
        "mem_pct": sample.get("mem"),
        "mem_total_mb": (_num(memory.get("total_kb")) or 0) / 1024,
        "storage_free_mb": (_num(storage.get("free_kb")) or 0) / 1024,
        "storage_total_mb": (_num(storage.get("total_kb")) or 0) / 1024,
        "uptime": _num(report.get("uptime_seconds")),
        "time": report.get("time"),
        "timezone": report.get("timezone"),
        "screen": report.get("screen") if isinstance(report.get("screen"), dict) else {},
        "foreground": report.get("foreground"),
        "fixes": list(reversed(state.get("fixes", [])))[:10],
        "history": state.get("history", []),
    }


def clock(at: float) -> str:
    return time.strftime("%b %d %H:%M", time.localtime(at))


def duration(seconds: Optional[float]) -> str:
    """3700 -> '1 h 1 min'."""
    if seconds is None:
        return "?"
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min"
    return f"{hours // 24} days"


def sparkline(history: list[dict[str, Any]], key: str, width: int = 240, height: int = 36) -> str:
    """SVG polyline points for one history series, scaled to its own range."""
    points = [(s["t"], s[key]) for s in history if _num(s.get(key)) is not None]
    if len(points) < 2:
        return ""
    t0, t1 = points[0][0], points[-1][0]
    low, high = min(v for _, v in points), max(v for _, v in points)
    span_t, span_v = (t1 - t0) or 1, (high - low) or 1
    return " ".join(
        f"{(t - t0) / span_t * width:.1f},{height - 2 - (v - low) / span_v * (height - 4):.1f}" for t, v in points
    )
