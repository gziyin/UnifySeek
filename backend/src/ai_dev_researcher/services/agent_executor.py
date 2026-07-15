from __future__ import annotations

import asyncio
import inspect
from uuid import UUID

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.agents.model import create_model_binding
from ai_dev_researcher.agents.orchestrator import create_research_agent
from ai_dev_researcher.agents.stream_adapter import map_framework_event
from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.domain.runs import RunStatus
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.paths import WorkspacePaths


class AgentResearchExecutor:
    """Run-level DeepAgents executor with event adaptation."""

    def __init__(
        self,
        *,
        settings: Settings,
        runs: RunRepository,
        artifacts: ArtifactRepository,
        evidence: EvidenceRepository,
        publisher: EventPublisher,
        paths: WorkspacePaths,
    ):
        self._settings = settings
        self._runs = runs
        self._artifacts = artifacts
        self._evidence = evidence
        self._publisher = publisher
        self._paths = paths

    async def __call__(self, run_id: UUID) -> None:
        run = await self._runs.get(run_id)
        if run is None:
            return

        context = RunContext(
            run_id=run.run_id,
            session_id=run.session_id,
            question=run.request.question,
            uploaded_artifact_ids=run.request.uploaded_artifact_ids,
            max_web_sources=run.request.max_web_sources,
            constraints=run.request.constraints,
            focus_areas=run.request.focus_areas,
            paths=self._paths,
            settings=self._settings,
        )
        store = EvidenceStore(
            run_id=run.run_id,
            session_id=run.session_id,
            evidence_repo=self._evidence,
            paths=self._paths,
        )

        try:
            await self._runs.update_status(run_id, RunStatus.RUNNING, started=True)
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="run.started",
                payload={"question_preview": run.request.question[:120]},
            )

            model_binding = create_model_binding(self._settings)
            agent = create_research_agent(context, model_binding, store, self._artifacts)
            input_payload = {
                "messages": [
                    {
                        "role": "user",
                        "content": run.request.question,
                    }
                ]
            }
            config = {"configurable": {"thread_id": str(run_id)}}

            report_artifact_id: str | None = None
            stream = agent.astream_events(input_payload, config=config, version="v3")
            if inspect.isawaitable(stream):
                stream = await stream

            async for raw in stream:
                if not isinstance(raw, dict):
                    continue
                event_type, actor, payload = map_framework_event(raw)
                if event_type is None:
                    continue

                if event_type == "tool.completed" and payload.get("discovered"):
                    discovered = payload["discovered"]
                    await self._publisher.publish(
                        session_id=run.session_id,
                        run_id=run_id,
                        event_type="source.discovered",
                        actor="web-researcher",
                        payload={
                            "evidence_id": discovered.get("evidence_id"),
                            "source_type": "web",
                            "title": discovered.get("title"),
                            "evidence_level": discovered.get("evidence_level", "search_snippet"),
                        },
                    )
                    await self._publisher.publish(
                        session_id=run.session_id,
                        run_id=run_id,
                        event_type="evidence.recorded",
                        actor="web-researcher",
                        payload={
                            "evidence_id": discovered.get("evidence_id"),
                            "locator": discovered.get("url"),
                        },
                    )

                if event_type == "tool.completed" and payload.get("recorded"):
                    recorded = payload["recorded"]
                    await self._publisher.publish(
                        session_id=run.session_id,
                        run_id=run_id,
                        event_type="source.discovered",
                        actor="document-analyst",
                        payload={
                            "evidence_id": recorded.get("evidence_id"),
                            "source_type": "document",
                            "title": recorded.get("title", "document"),
                            "evidence_level": "user_document",
                        },
                    )
                    await self._publisher.publish(
                        session_id=run.session_id,
                        run_id=run_id,
                        event_type="evidence.recorded",
                        actor="document-analyst",
                        payload={
                            "evidence_id": recorded.get("evidence_id"),
                            "locator": recorded.get("locator"),
                        },
                    )

                if event_type == "tool.completed" and payload.get("tool_name") == "submit_research_report":
                    artifact_id = payload.get("artifact_id")
                    if artifact_id:
                        report_artifact_id = str(artifact_id)
                        await self._publisher.publish(
                            session_id=run.session_id,
                            run_id=run_id,
                            event_type="artifact.created",
                            payload={
                                "artifact_id": report_artifact_id,
                                "artifact_kind": "report",
                                "display_name": "research-report.md",
                            },
                        )
                        await self._publisher.publish(
                            session_id=run.session_id,
                            run_id=run_id,
                            event_type="report.ready",
                            payload={"artifact_id": report_artifact_id},
                        )

                await self._publisher.publish(
                    session_id=run.session_id,
                    run_id=run_id,
                    event_type=event_type,
                    actor=actor,
                    payload={k: v for k, v in payload.items() if k not in {"discovered", "recorded"}},
                )

            if not report_artifact_id:
                raise RuntimeError("agent finished without submit_research_report")

            await self._runs.update_status(
                run_id,
                RunStatus.SUCCEEDED,
                finished=True,
                report_artifact_id=UUID(report_artifact_id),
            )
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="run.succeeded",
                payload={"report_artifact_id": report_artifact_id},
            )
        except asyncio.CancelledError:
            current = await self._runs.get(run_id)
            if current and current.status not in {RunStatus.CANCELLED, RunStatus.SUCCEEDED}:
                await self._runs.update_status(
                    run_id,
                    RunStatus.CANCELLED,
                    finished=True,
                    error_code="CANCELLED",
                    error_message="Cancelled by user",
                )
                await self._publisher.publish(
                    session_id=run.session_id,
                    run_id=run_id,
                    event_type="run.cancelled",
                    payload={"reason": "user_cancelled"},
                )
            raise
        except Exception as exc:  # noqa: BLE001
            await self._runs.update_status(
                run_id,
                RunStatus.FAILED,
                finished=True,
                error_code="RUN_FAILED",
                error_message=str(exc)[:500],
            )
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="run.failed",
                payload={
                    "code": "RUN_FAILED",
                    "message": "Research run failed",
                    "retryable": False,
                },
            )
