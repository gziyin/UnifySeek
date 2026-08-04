from __future__ import annotations

from dataclasses import dataclass

import aiosqlite
from fastapi import Request

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.run_service import RunService
from ai_dev_researcher.services.session_service import SessionService
from ai_dev_researcher.services.task_manager import TaskManager
from ai_dev_researcher.services.upload_service import UploadService
from ai_dev_researcher.storage.paths import WorkspacePaths


@dataclass
class AppState:
    settings: Settings
    conn: aiosqlite.Connection
    paths: WorkspacePaths
    sessions: SessionRepository
    runs: RunRepository
    artifacts: ArtifactRepository
    events: EventRepository
    evidence: EvidenceRepository
    publisher: EventPublisher
    session_service: SessionService
    upload_service: UploadService
    run_service: RunService
    task_manager: TaskManager
    vector_store: object | None = None
    knowledge_index: object | None = None


def get_app_state(request: Request) -> AppState:
    return request.app.state.container
