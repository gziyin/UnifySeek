from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class InstanceLock:
    """Exclusive file lock that prevents two backend processes sharing a workspace."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: Any = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        file = path.open("a+", encoding="ascii")
        try:
            if file.tell() == 0:
                file.write("\n")
                file.flush()
            file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            file.close()
            raise RuntimeError(
                f"another backend instance is already running (lock: {path})"
            ) from exc
        self._file = file

    def release(self) -> None:
        if self._file is None:
            return
        file = self._file
        self._file = None
        file.close()

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.release()
