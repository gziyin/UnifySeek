from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from ai_dev_researcher.domain.sessions import utc_now


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


ACTIVE_RUN_STATUSES = {
    RunStatus.PENDING,
    RunStatus.RUNNING,
    RunStatus.CANCELLING,
}

TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.INTERRUPTED,
    RunStatus.CANCELLED,
}

ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.INTERRUPTED, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLING,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
    },
    RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.INTERRUPTED},
}


class ResearchRequest(BaseModel):
    question: str = Field()
    constraints: list[str] = Field(default_factory=list, max_length=10)
    focus_areas: list[str] = Field(default_factory=list, max_length=10)
    max_web_sources: int = Field(default=8, ge=3, le=15)
    uploaded_artifact_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question must not be empty")
        return cleaned


class Run(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    status: RunStatus = RunStatus.PENDING
    request: ResearchRequest
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    report_artifact_id: UUID | None = None

    def assert_can_transition(self, target: RunStatus) -> None:
        allowed = ALLOWED_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise ValueError(f"invalid transition {self.status} -> {target}")


def request_to_json(request: ResearchRequest) -> dict[str, Any]:
    return request.model_dump(mode="json")
