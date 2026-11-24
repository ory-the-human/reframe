import json
import os
from pathlib import Path
from typing import Any, Optional


def read_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write atomically with owner-only permissions; these files hold credentials."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
