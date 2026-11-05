import json
from pathlib import Path
from typing import Any


def read_json_cache(path: Path) -> Any | None:
    """Load JSON from disk; discard and return None if the file is corrupt."""
    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        path.unlink(missing_ok=True)
        return None


def write_json_cache(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
