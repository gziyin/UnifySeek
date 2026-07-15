from __future__ import annotations

from pathlib import Path


def ensure_within_root(path: Path, root: Path) -> Path:
    """Resolve path and reject escapes outside root (Windows-safe)."""
    resolved_root = root.resolve()
    resolved = path.resolve(strict=False)
    try:
        if not resolved.is_relative_to(resolved_root):
            raise ValueError("path escapes workspace root")
    except ValueError as exc:
        raise ValueError("path escapes workspace root") from exc
    return resolved


def reject_unsafe_user_path(raw: str) -> None:
    """Reject absolute / UNC / drive-letter / parent traversal inputs."""
    value = raw.strip()
    if not value:
        raise ValueError("empty path")
    if value.startswith(("\\\\", "//")):
        raise ValueError("unc path rejected")
    if len(value) >= 2 and value[1] == ":":
        raise ValueError("drive letter path rejected")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError("absolute path rejected")
    if ".." in candidate.parts:
        raise ValueError("parent traversal rejected")
