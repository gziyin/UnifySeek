from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from ai_dev_researcher.domain.artifacts import Artifact, ArtifactKind, ParseStatus
from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.reports import (
    ResearchClaim,
    ResearchReport,
    ReportSection,
)
from ai_dev_researcher.domain.runs import RunStatus
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.storage.artifacts import (
    atomic_write_text,
    collect_claims,
    render_report_markdown,
)
from ai_dev_researcher.storage.paths import WorkspacePaths


class FakeResearchExecutor:
    """Deterministic no-LLM executor used to validate the vertical slice."""

    def __init__(
        self,
        *,
        runs: RunRepository,
        artifacts: ArtifactRepository,
        evidence: EvidenceRepository,
        publisher: EventPublisher,
        paths: WorkspacePaths,
    ):
        self._runs = runs
        self._artifacts = artifacts
        self._evidence = evidence
        self._publisher = publisher
        self._paths = paths

    async def __call__(self, run_id: UUID) -> None:
        run = await self._runs.get(run_id)
        if run is None:
            return
        try:
            await self._runs.update_status(run_id, RunStatus.RUNNING, started=True)
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="run.started",
                payload={"question_preview": run.request.question[:120]},
            )
            await asyncio.sleep(0.05)

            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="plan.updated",
                actor="research-orchestrator",
                payload={
                    "items": [
                        {"id": "1", "content": "检索公开网页资料", "status": "in_progress"},
                        {"id": "2", "content": "分析上传文档", "status": "pending"},
                        {"id": "3", "content": "汇总并提交报告", "status": "pending"},
                    ]
                },
            )
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="agent.started",
                actor="research-orchestrator",
                payload={
                    "agent_name": "web-researcher",
                    "task_id": "task-web-1",
                    "description": "搜索 DeepAgents 相关公开资料",
                },
            )
            await asyncio.sleep(0.05)

            web_ids, _, _ = await self._evidence.allocate_ids(run_id, web_count=1)
            web_id = web_ids[0]
            web_evidence = EvidenceRecord(
                id=web_id,
                run_id=run_id,
                source_type="web",
                evidence_level="first_party",
                title="DeepAgents Overview (fake)",
                locator="https://example.com/deepagents",
                canonical_url="https://example.com/deepagents",
                publisher_key="example.com",
                excerpt="DeepAgents provides an orchestrator with specialized subagents.",
                query="DeepAgents architecture",
                result_rank=1,
            )
            await self._evidence.create(web_evidence)
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="source.discovered",
                actor="web-researcher",
                payload={
                    "evidence_id": web_id,
                    "source_type": "web",
                    "title": web_evidence.title,
                    "evidence_level": web_evidence.evidence_level,
                },
            )
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="evidence.recorded",
                actor="web-researcher",
                payload={"evidence_id": web_id, "locator": web_evidence.locator},
            )
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="agent.completed",
                actor="research-orchestrator",
                payload={
                    "agent_name": "web-researcher",
                    "task_id": "task-web-1",
                    "summary": "采集到 1 条网页证据",
                },
            )

            doc_citation = web_id
            if run.request.uploaded_artifact_ids:
                await self._publisher.publish(
                    session_id=run.session_id,
                    run_id=run_id,
                    event_type="agent.started",
                    actor="research-orchestrator",
                    payload={
                        "agent_name": "document-analyst",
                        "task_id": "task-doc-1",
                        "description": "阅读授权上传文档",
                    },
                )
                _, doc_ids, _ = await self._evidence.allocate_ids(run_id, document_count=1)
                doc_id = doc_ids[0]
                uploaded = await self._artifacts.get(run.request.uploaded_artifact_ids[0])
                title = uploaded.display_name if uploaded else "uploaded document"
                doc_evidence = EvidenceRecord(
                    id=doc_id,
                    run_id=run_id,
                    source_type="document",
                    evidence_level="user_document",
                    title=title,
                    locator="lines 1-3",
                    excerpt="用户笔记提到 DeepAgents 适合快速搭建研究编排。",
                    line_start=1,
                    line_end=3,
                )
                await self._evidence.create(doc_evidence)
                doc_citation = doc_id
                await self._publisher.publish(
                    session_id=run.session_id,
                    run_id=run_id,
                    event_type="source.discovered",
                    actor="document-analyst",
                    payload={
                        "evidence_id": doc_id,
                        "source_type": "document",
                        "title": title,
                        "evidence_level": "user_document",
                    },
                )
                await self._publisher.publish(
                    session_id=run.session_id,
                    run_id=run_id,
                    event_type="agent.completed",
                    actor="research-orchestrator",
                    payload={
                        "agent_name": "document-analyst",
                        "task_id": "task-doc-1",
                        "summary": "采集到 1 条文档证据",
                    },
                )

            claim_main = ResearchClaim(
                id="C1",
                statement="DeepAgents 适合用主智能体编排、专业子智能体取证的研究工作流。",
                citation_ids=[web_id],
                confidence="medium",
            )
            claim_doc = ResearchClaim(
                id="C2",
                statement="结合用户资料可以更快界定个人项目的适用边界。",
                citation_ids=[doc_citation],
                confidence="medium",
            )
            claim_rec = ResearchClaim(
                id="C3",
                statement="两周内优先打通证据账本、报告校验与实时事件协议。",
                citation_ids=[web_id],
                confidence="medium",
            )
            report = ResearchReport(
                title="DeepAgents 技术调研（Fake Slice）",
                executive_summary_claim_ids=["C1"],
                sections=[
                    ReportSection(heading="适用边界", claims=[claim_main, claim_doc]),
                ],
                unknowns=["真实官方文档版本差异尚未核验（fake 模式）"],
                recommendations=[claim_rec],
            )
            claims = collect_claims(report)
            markdown = render_report_markdown(report, claims)

            report_artifact_id = uuid4()
            report_path = self._paths.report_path(run.session_id, run_id, report_artifact_id)
            atomic_write_text(report_path, markdown, root=self._paths.sessions_root)
            artifact = Artifact(
                artifact_id=report_artifact_id,
                session_id=run.session_id,
                run_id=run_id,
                kind=ArtifactKind.REPORT,
                display_name="research-report.md",
                mime_type="text/markdown",
                size_bytes=len(markdown.encode("utf-8")),
                parse_status=ParseStatus.SKIPPED,
                original_storage_path=str(report_path),
            )
            await self._artifacts.create(artifact)
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="artifact.created",
                payload={
                    "artifact_id": str(report_artifact_id),
                    "artifact_kind": "report",
                    "display_name": artifact.display_name,
                },
            )
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="report.ready",
                payload={"artifact_id": str(report_artifact_id)},
            )
            await self._runs.update_status(
                run_id,
                RunStatus.SUCCEEDED,
                finished=True,
                report_artifact_id=report_artifact_id,
            )
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="run.succeeded",
                payload={"report_artifact_id": str(report_artifact_id)},
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
