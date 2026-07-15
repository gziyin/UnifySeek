from pathlib import Path

from ai_dev_researcher.core.security import reject_unsafe_user_path
from ai_dev_researcher.storage.paths import WorkspacePaths
import pytest


def test_reject_unsafe_paths():
    with pytest.raises(ValueError):
        reject_unsafe_user_path("C:/Windows/system32")
    with pytest.raises(ValueError):
        reject_unsafe_user_path("\\\\server\\share")
    with pytest.raises(ValueError):
        reject_unsafe_user_path("../escape.txt")
    reject_unsafe_user_path("notes.txt")


def test_workspace_paths_stay_inside_root(tmp_path: Path):
    from uuid import uuid4

    paths = WorkspacePaths(tmp_path)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    report = paths.report_path(session_id, run_id, uuid4())
    assert report.is_relative_to(tmp_path.resolve())
