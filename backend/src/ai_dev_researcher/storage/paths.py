from __future__ import annotations

from pathlib import Path
from uuid import UUID

from ai_dev_researcher.core.security import ensure_within_root
from ai_dev_researcher.domain.sessions import session_dir_name


class WorkspacePaths:
    def __init__(self, sessions_root: Path, knowledge_base_root: Path | None = None):
        self.sessions_root = sessions_root
        self.knowledge_base_root = knowledge_base_root

    def session_dir(self, session_id: UUID, display_name: str | None = None) -> Path:
        """Resolve the on-disk directory for a session.

        Resolution order (sticky, backward compatible):
        1. ``display_name`` provided -> ``<slug>-<8位短uuid>`` (new-style naming).
        2. No ``display_name`` but legacy UUID dir exists -> keep it (存量兼容).
        3. No ``display_name`` but a slug dir for this session exists -> reuse it
           (callers that only know ``session_id``, e.g. uploads after first run).
        4. Otherwise fall back to the legacy ``str(session_id)`` name.
        """
        name = self._resolve_session_dir_name(session_id, display_name)
        path = self.sessions_root / name
        return ensure_within_root(path, self.sessions_root)

    def _resolve_session_dir_name(self, session_id: UUID, display_name: str | None) -> str:
        # Sticky resolution (backward compatible, never splits an existing session):
        # 1. 存量纯 UUID 目录优先：一旦存在，绝不切换到 slug 目录（不破坏既有数据）。
        legacy = str(session_id)
        if (self.sessions_root / legacy).exists():
            return legacy
        # 2. 已命名 session（此前 run 已创建 slug 目录）→ 仅凭 session_id 也复用该目录。
        existing_slug = self._find_slug_dir(session_id)
        if existing_slug is not None:
            return existing_slug
        # 3. 新 session 首次 run 且携带 display_name → slug-8位短uuid 命名。
        if display_name:
            return session_dir_name(display_name, session_id)
        # 4. 兜底：新 session 尚无任何目录 → 沿用 legacy 命名（调用方后续再命名）。
        return legacy

    def _find_slug_dir(self, session_id: UUID) -> str | None:
        """Return the slug-style directory name for a session, if one exists."""
        short = session_id.hex[:8]
        if not self.sessions_root.exists():
            return None
        for entry in self.sessions_root.iterdir():
            if entry.is_dir() and entry.name.endswith(f"-{short}"):
                return entry.name
        return None

    def uploads_dir(self, session_id: UUID, display_name: str | None = None) -> Path:
        return self.session_dir(session_id, display_name) / "uploads"

    def normalized_dir(self, session_id: UUID, display_name: str | None = None) -> Path:
        return self.session_dir(session_id, display_name) / "normalized"

    def run_dir(self, session_id: UUID, run_id: UUID, display_name: str | None = None) -> Path:
        path = self.session_dir(session_id, display_name) / "runs" / str(run_id)
        return ensure_within_root(path, self.sessions_root)

    def evidence_dir(self, session_id: UUID, run_id: UUID, display_name: str | None = None) -> Path:
        return self.run_dir(session_id, run_id, display_name) / "evidence"

    def reports_dir(self, session_id: UUID, run_id: UUID, display_name: str | None = None) -> Path:
        return self.run_dir(session_id, run_id, display_name) / "reports"

    def upload_path(self, session_id: UUID, artifact_id: UUID, display_name: str | None = None) -> Path:
        return self.uploads_dir(session_id, display_name) / f"{artifact_id}.bin"

    def normalized_path(self, session_id: UUID, artifact_id: UUID, display_name: str | None = None) -> Path:
        return self.normalized_dir(session_id, display_name) / f"{artifact_id}.txt"

    def report_path(self, session_id: UUID, run_id: UUID, artifact_id: UUID, display_name: str | None = None) -> Path:
        return self.reports_dir(session_id, run_id, display_name) / f"{artifact_id}.md"

    def knowledge_base_dir(self) -> Path:
        if self.knowledge_base_root is None:
            raise ValueError("knowledge_base_root is not configured")
        return self.knowledge_base_root.resolve()

    def knowledge_base_path(self, relative: str) -> Path:
        """Resolve a knowledge-base-relative path, rejecting escapes."""
        root = self.knowledge_base_dir()
        return ensure_within_root(root / relative, root)

    def ensure_session_layout(self, session_id: UUID, display_name: str | None = None) -> None:
        self.uploads_dir(session_id, display_name).mkdir(parents=True, exist_ok=True)
        self.normalized_dir(session_id, display_name).mkdir(parents=True, exist_ok=True)

    def ensure_run_layout(self, session_id: UUID, run_id: UUID, display_name: str | None = None) -> None:
        self.ensure_session_layout(session_id, display_name)
        self.evidence_dir(session_id, run_id, display_name).mkdir(parents=True, exist_ok=True)
        (self.run_dir(session_id, run_id, display_name) / "temp").mkdir(parents=True, exist_ok=True)
        self.reports_dir(session_id, run_id, display_name).mkdir(parents=True, exist_ok=True)
