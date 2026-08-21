from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ai_dev_researcher.domain.sessions import utc_now


class EvidenceRecord(BaseModel):
    id: str
    run_id: UUID
    source_type: Literal["web", "document", "knowledge_base"]
    artifact_id: UUID | None = None
    evidence_level: Literal[
        "official_primary",
        "first_party",
        "secondary",
        "user_document",
        "search_snippet",
    ]
    title: str
    locator: str
    canonical_url: str | None = None
    publisher_key: str | None = None
    excerpt: str
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    query: str | None = None
    result_rank: int | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
