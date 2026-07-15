from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from ai_dev_researcher.domain.sessions import utc_now


class ArtifactKind(StrEnum):
    UPLOAD = "upload"
    REPORT = "report"


class ParseStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Artifact(BaseModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    run_id: UUID | None = None
    kind: ArtifactKind
    display_name: str
    mime_type: str
    size_bytes: int = 0
    parse_status: ParseStatus = ParseStatus.SKIPPED
    original_storage_path: str | None = None
    normalized_storage_path: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
