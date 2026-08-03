from __future__ import annotations

from pathlib import Path
from uuid import UUID

from ai_dev_researcher.core.security import ensure_within_root


class WorkspacePaths:
    def __init__(self, sessions_root: Path, knowledge_base_root: Path | None = None):
        self.sessions_root = sessions_root
        self.knowledge_base_root = knowledge_base_root

    def session_dir(self, session_id: UUID) -> Path:
        path = self.sessions_root / str(session_id)
        return ensure_within_root(path, self.sessions_root)

    def uploads_dir(self, session_id: UUID) -> Path:
        return self.session_dir(session_id) / "uploads"

    def normalized_dir(self, session_id: UUID) -> Path:
        return self.session_dir(session_id) / "normalized"

    def run_dir(self, session_id: UUID, run_id: UUID) -> Path:
        path = self.session_dir(session_id) / "runs" / str(run_id)
        return ensure_within_root(path, self.sessions_root)

    def evidence_dir(self, session_id: UUID, run_id: UUID) -> Path:
        return self.run_dir(session_id, run_id) / "evidence"

    def reports_dir(self, session_id: UUID, run_id: UUID) -> Path:
        return self.run_dir(session_id, run_id) / "reports"

    def upload_path(self, session_id: UUID, artifact_id: UUID) -> Path:
        return self.uploads_dir(session_id) / f"{artifact_id}.bin"

    def normalized_path(self, session_id: UUID, artifact_id: UUID) -> Path:
        return self.normalized_dir(session_id) / f"{artifact_id}.txt"

    def report_path(self, session_id: UUID, run_id: UUID, artifact_id: UUID) -> Path:
        return self.reports_dir(session_id, run_id) / f"{artifact_id}.md"

    def knowledge_base_dir(self) -> Path:
        if self.knowledge_base_root is None:
            raise ValueError("knowledge_base_root is not configured")
        return self.knowledge_base_root.resolve()

    def knowledge_base_path(self, relative: str) -> Path:
        """Resolve a knowledge-base-relative path, rejecting escapes."""
        root = self.knowledge_base_dir()
        return ensure_within_root(root / relative, root)

    def ensure_session_layout(self, session_id: UUID) -> None:
        self.uploads_dir(session_id).mkdir(parents=True, exist_ok=True)
        self.normalized_dir(session_id).mkdir(parents=True, exist_ok=True)

    def ensure_run_layout(self, session_id: UUID, run_id: UUID) -> None:
        self.ensure_session_layout(session_id)
        self.evidence_dir(session_id, run_id).mkdir(parents=True, exist_ok=True)
        (self.run_dir(session_id, run_id) / "temp").mkdir(parents=True, exist_ok=True)
        self.reports_dir(session_id, run_id).mkdir(parents=True, exist_ok=True)
