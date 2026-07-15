from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from ai_dev_researcher.domain.sessions import utc_now

EventType = Literal[
    "run.started",
    "plan.updated",
    "agent.started",
    "agent.completed",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "source.discovered",
    "evidence.recorded",
    "artifact.created",
    "report.ready",
    "run.succeeded",
    "run.failed",
    "run.cancelling",
    "run.cancelled",
    "run.interrupted",
    "heartbeat",
]


class ResearchEvent(BaseModel):
    protocol_version: Literal["1.0"] = "1.0"
    event_id: UUID = Field(default_factory=uuid4)
    seq: int
    session_id: UUID
    run_id: UUID
    type: EventType
    occurred_at: datetime = Field(default_factory=utc_now)
    actor: str = "system"
    payload: dict[str, Any] = Field(default_factory=dict)
