from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from ai_dev_researcher.domain.runs import OutputMode, ResearchRequest, RunStatus


class ErrorResponse(BaseModel):
    code: str
    message: str
    retryable: bool = False


class SessionResponse(BaseModel):
    session_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime


class ArtifactResponse(BaseModel):
    artifact_id: UUID
    session_id: UUID
    run_id: UUID | None = None
    kind: str
    display_name: str
    mime_type: str
    size_bytes: int
    parse_status: str
    created_at: datetime


class CreateRunRequest(ResearchRequest):
    pass


class RunResponse(BaseModel):
    run_id: UUID
    session_id: UUID
    status: RunStatus
    question: str
    output_mode: OutputMode
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    report_artifact_id: UUID | None = None
    last_seq: int = 0


class EventResponse(BaseModel):
    protocol_version: str
    event_id: UUID
    seq: int
    session_id: UUID
    run_id: UUID
    type: str
    occurred_at: datetime
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)


class EventsListResponse(BaseModel):
    run_id: UUID
    events: list[EventResponse]
    last_seq: int


class ArtifactContentResponse(BaseModel):
    artifact_id: UUID
    kind: str
    content: str


class ReportJsonResponse(BaseModel):
    artifact_id: UUID
    report: dict | None = None
    degraded: bool = False
    reason: str | None = None
