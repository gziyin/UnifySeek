from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.storage.paths import WorkspacePaths


@dataclass
class RunContext:
    run_id: UUID
    session_id: UUID
    question: str
    uploaded_artifact_ids: list[UUID]
    max_web_sources: int
    paths: WorkspacePaths
    settings: Settings
    constraints: list[str] = field(default_factory=list)
    focus_areas: list[str] = field(default_factory=list)
    max_tool_calls: int = 0
    max_elapsed_seconds: float = 0.0
    knowledge_context: str = ''
