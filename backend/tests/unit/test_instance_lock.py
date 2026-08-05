from __future__ import annotations

from pathlib import Path

import pytest

from ai_dev_researcher.core.instance_lock import InstanceLock


def test_second_acquire_fails_until_first_releases(tmp_path: Path) -> None:
    lock_path = tmp_path / "instance.lock"

    with InstanceLock(lock_path):
        with pytest.raises(RuntimeError, match="already running"):
            with InstanceLock(lock_path):
                pass

    with InstanceLock(lock_path):
        pass
