from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.services.agent_executor import AgentResearchExecutor
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.fake_executor import FakeResearchExecutor
from ai_dev_researcher.storage.paths import WorkspacePaths

RunExecutor = Callable[[UUID], Awaitable[None]]


def create_run_executor(
    *,
    settings: Settings,
    runs: RunRepository,
    artifacts: ArtifactRepository,
    evidence: EvidenceRepository,
    publisher: EventPublisher,
    paths: WorkspacePaths,
    vector_store=None,
) -> RunExecutor:
    if settings.fake_agent_mode or not settings.deepseek_api_key:
        return FakeResearchExecutor(
            runs=runs,
            artifacts=artifacts,
            evidence=evidence,
            publisher=publisher,
            paths=paths,
        )
    return AgentResearchExecutor(
        settings=settings,
        runs=runs,
        artifacts=artifacts,
        evidence=evidence,
        publisher=publisher,
        paths=paths,
        vector_store=vector_store,
    )
