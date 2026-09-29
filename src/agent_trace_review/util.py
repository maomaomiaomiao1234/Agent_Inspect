import hashlib
import json
import math
import posixpath
import re
from datetime import datetime, timezone
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    data = value if isinstance(value, bytes) else canonical(value).encode()
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value if value >= 0 else None


def timestamp(value: Any) -> float | None:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
        except (ValueError, OverflowError):
            return None
    return number(value)


def relative_path(path: Any, directory: str) -> str | None:
    if not isinstance(path, str) or not path or "[redacted" in path:
        return None
    path = path.replace("\\", "/")
    root = directory.replace("\\", "/").rstrip("/")
    if root and path.startswith(root + "/"):
        path = path[len(root) + 1 :]
    return posixpath.normpath(path)


SECRET_KEY = re.compile(
    r"^(?:api[_-]?key|authorization|password|access[_-]?token|refresh[_-]?token|secret)$", re.I
)
SECRET_TEXT = re.compile(
    r"(?i)(?:Bearer\s+[A-Za-z0-9_.~+/=-]{8,}|sk-[a-zA-Z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})"
)


def redact(value: Any) -> Any:
    """Conservative local redaction. Never read credentials or referenced local files."""
    if isinstance(value, dict):
        return {
            key: "[redacted:secret]" if SECRET_KEY.match(key) else redact(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_TEXT.sub("[redacted:secret]", value)
    return value
