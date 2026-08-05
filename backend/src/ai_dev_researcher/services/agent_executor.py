from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from uuid import UUID

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.agents.model import create_model_binding
from ai_dev_researcher.agents.orchestrator import create_research_agent
from ai_dev_researcher.agents.stream_adapter import map_framework_event
from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.domain.runs import ALLOWED_TRANSITIONS, Run, RunStatus
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.tools.knowledge_base import (
    record_knowledge_base_evidence_impl,
    search_knowledge_base_impl,
)
from ai_dev_researcher.tools.report_submitter import submit_research_report_impl

logger = logging.getLogger(__name__)


class _BudgetExceededError(RuntimeError):
    """Marker so the executor can attribute a run failure to budget controls."""


@dataclass
class _StreamAttemptResult:
    report_artifact_id: str | None = None
    budget_reason: str | None = None
    degraded_reason: str | None = None


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
        vector_store=None,
        knowledge_index=None,
    ):
        self._settings = settings
        self._runs = runs
        self._artifacts = artifacts
        self._evidence = evidence
        self._publisher = publisher
        self._paths = paths
        self._vector_store = vector_store
        self._knowledge_index = knowledge_index

    async def __call__(self, run_id: UUID) -> None:
        run = await self._runs.get(run_id)
        if run is None:
            return

        max_tool_calls, max_elapsed_seconds = self._resolve_budget(run)
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
            max_tool_calls=max_tool_calls,
            max_elapsed_seconds=max_elapsed_seconds,
        )
        store = EvidenceStore(
            run_id=run.run_id,
            session_id=run.session_id,
            evidence_repo=self._evidence,
            paths=self._paths,
        )

        report_artifact_id: str | None = None
        try:
            await self._runs.update_status(run_id, RunStatus.RUNNING, started=True)
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="run.started",
                payload={"question_preview": run.request.question[:120]},
            )

            await self._prefetch_knowledge(context, store)

            model_binding = create_model_binding(self._settings)
            agent = create_research_agent(
                context,
                model_binding,
                store,
                self._artifacts,
                vector_store=self._vector_store,
                knowledge_index=self._knowledge_index,
            )
            input_payload = {
                "messages": [
                    {
                        "role": "user",
                        "content": run.request.question,
                    }
                ]
            }
            config = {"configurable": {"thread_id": str(run_id)}}

            result = await self._run_agent_attempt(
                run=run,
                context=context,
                store=store,
                agent=agent,
                input_payload=input_payload,
                config=config,
            )

            if result.report_artifact_id and result.degraded_reason:
                report_artifact_id = result.report_artifact_id
                raise RuntimeError(
                    f"submit_research_report returned degraded report: {result.degraded_reason}"
                )
            if result.report_artifact_id:
                report_artifact_id = result.report_artifact_id
            elif result.budget_reason:
                report_artifact_id = await self._write_degraded_report(
                    context, store, result.budget_reason
                )
                await self._publish_report_ready(
                    run,
                    report_artifact_id,
                    degraded=True,
                    reason=result.budget_reason,
                )
                raise _BudgetExceededError(result.budget_reason)
            else:
                # 流结束但未提交：保留已有 evidence，换一个 thread 做一次受控重试。
                retry_input = {
                    "messages": [
                        {"role": "user", "content": run.request.question},
                        {
                            "role": "user",
                            "content": (
                                "上一轮已结束但未提交研究报告。请不要继续广泛调研，"
                                "仅基于当前证据账本立即调用 submit_research_report 提交报告；"
                                "若证据不足，也请提交一份尽量完整但标注低置信度的报告。"
                            ),
                        },
                    ]
                }
                retry_config = {
                    "configurable": {"thread_id": f"{run_id}:retry"}
                }
                retry_result = await self._run_agent_attempt(
                    run=run,
                    context=context,
                    store=store,
                    agent=agent,
                    input_payload=retry_input,
                    config=retry_config,
                )
                if retry_result.report_artifact_id and retry_result.degraded_reason:
                    report_artifact_id = retry_result.report_artifact_id
                    raise RuntimeError(
                        f"submit_research_report returned degraded report: {retry_result.degraded_reason}"
                    )
                if retry_result.report_artifact_id:
                    report_artifact_id = retry_result.report_artifact_id
                elif retry_result.budget_reason:
                    report_artifact_id = await self._write_degraded_report(
                        context, store, retry_result.budget_reason
                    )
                    await self._publish_report_ready(
                        run,
                        report_artifact_id,
                        degraded=True,
                        reason=retry_result.budget_reason,
                    )
                    raise _BudgetExceededError(retry_result.budget_reason)
                else:
                    raise RuntimeError(
                        "agent finished without submit_research_report after one controlled retry"
                    )

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
            if current and RunStatus.CANCELLED in ALLOWED_TRANSITIONS.get(current.status, set()):
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
            elif current:
                logger.warning(
                    "preserving run %s status %s instead of transitioning to %s",
                    run_id,
                    current.status,
                    RunStatus.CANCELLED,
                )
            raise
        except _BudgetExceededError as exc:
            await self._fail_run_if_allowed(
                run_id,
                error_code="BUDGET_EXCEEDED",
                error_message=str(exc)[:500],
                event_code="BUDGET_EXCEEDED",
                event_message="Research run budget exceeded",
                reason=str(exc),
                report_artifact_id=report_artifact_id,
            )
        except Exception as exc:  # noqa: BLE001
            await self._fail_run_if_allowed(
                run_id,
                error_code="RUN_FAILED",
                error_message=str(exc)[:500],
                event_code="RUN_FAILED",
                event_message="Research run failed",
                reason=str(exc),
                report_artifact_id=report_artifact_id,
            )

    async def _fail_run_if_allowed(
        self,
        run_id: UUID,
        *,
        error_code: str,
        error_message: str,
        event_code: str,
        event_message: str,
        reason: str,
        report_artifact_id: str | None = None,
    ) -> bool:
        current = await self._runs.get(run_id)
        if current is None:
            return False
        if RunStatus.FAILED not in ALLOWED_TRANSITIONS.get(current.status, set()):
            logger.warning(
                "preserving run %s status %s instead of transitioning to %s",
                run_id,
                current.status,
                RunStatus.FAILED,
            )
            return False
        report_id = UUID(report_artifact_id) if report_artifact_id else None
        await self._runs.update_status(
            run_id,
            RunStatus.FAILED,
            finished=True,
            error_code=error_code,
            error_message=error_message,
            report_artifact_id=report_id,
        )
        await self._publisher.publish(
            session_id=current.session_id,
            run_id=run_id,
            event_type="run.failed",
            payload={
                "code": event_code,
                "message": event_message,
                "reason": reason,
                "retryable": False,
                "report_artifact_id": str(report_id) if report_id else None,
            },
        )
        return True

    async def _run_agent_attempt(
        self,
        *,
        run: Run,
        context: RunContext,
        store: EvidenceStore,
        agent,
        input_payload: dict,
        config: dict,
    ) -> _StreamAttemptResult:
        report_artifact_id: str | None = None
        # 维护 tool_call_id -> input_summary，用于在 tool.completed 上附带脱敏输入摘要。
        tool_inputs: dict[str, str] = {}
        started = time.monotonic()
        tool_call_count = 0
        # 使用 v2 经典事件协议（on_tool_start/on_tool_end，{event,name,data,run_id}）。
        # langgraph 1.2.10 的 v3 是实验性 run-stream 协议（{type,method,params}），
        # 与 stream_adapter.map_framework_event 的解析格式不兼容（M1 实测发现）。
        stream = agent.astream_events(input_payload, config=config, version="v2")
        if inspect.isawaitable(stream):
            stream = await stream

        async for raw in stream:
            if not isinstance(raw, dict):
                continue
            event_type, actor, payload = map_framework_event(raw)
            if event_type is None:
                continue

            if event_type == "tool.started":
                tool_call_count += 1
                tool_inputs[payload.get("tool_call_id", "")] = payload.get(
                    "input_summary", ""
                )
            if event_type == "tool.completed":
                payload["tool_input"] = tool_inputs.get(
                    payload.get("tool_call_id", ""), ""
                )

            if event_type == "tool.completed" and payload.get("discovered"):
                discovered = payload["discovered"]
                await self._publisher.publish(
                    session_id=run.session_id,
                    run_id=run.run_id,
                    event_type="source.discovered",
                    actor="web-researcher",
                    payload={
                        "evidence_id": discovered.get("evidence_id"),
                        "source_type": "web",
                        "title": discovered.get("title"),
                        "url": discovered.get("url"),
                        "query": discovered.get("query"),
                        "publisher_key": discovered.get("publisher_key"),
                        "result_rank": discovered.get("result_rank"),
                        "evidence_level": discovered.get("evidence_level", "search_snippet"),
                    },
                )
                await self._publisher.publish(
                    session_id=run.session_id,
                    run_id=run.run_id,
                    event_type="evidence.recorded",
                    actor="web-researcher",
                    payload={
                        "evidence_id": discovered.get("evidence_id"),
                        "source_type": "web",
                        "locator": discovered.get("url"),
                        "excerpt": discovered.get("snippet"),
                    },
                )

            if event_type == "tool.completed" and payload.get("recorded"):
                recorded = payload["recorded"]
                tool_name = payload.get("tool_name", "")
                if tool_name == "record_knowledge_base_evidence":
                    actor = "document-analyst"
                    source_type = "knowledge_base"
                    source_title = recorded.get("title", "knowledge_base")
                    discover_payload: dict = {
                        "evidence_id": recorded.get("evidence_id"),
                        "source_type": source_type,
                        "title": source_title,
                        "path": recorded.get("path"),
                        "evidence_level": "first_party",
                    }
                else:
                    actor = "document-analyst"
                    source_type = "document"
                    source_title = recorded.get("title", "document")
                    discover_payload = {
                        "evidence_id": recorded.get("evidence_id"),
                        "source_type": source_type,
                        "title": source_title,
                        "artifact_id": recorded.get("artifact_id"),
                        "display_name": recorded.get("display_name"),
                        "evidence_level": "user_document",
                    }
                await self._publisher.publish(
                    session_id=run.session_id,
                    run_id=run.run_id,
                    event_type="source.discovered",
                    actor=actor,
                    payload=discover_payload,
                )
                await self._publisher.publish(
                    session_id=run.session_id,
                    run_id=run.run_id,
                    event_type="evidence.recorded",
                    actor=actor,
                    payload={
                        "evidence_id": recorded.get("evidence_id"),
                        "source_type": source_type,
                        "locator": recorded.get("locator"),
                        "line_start": recorded.get("line_start"),
                        "line_end": recorded.get("line_end"),
                        "page": recorded.get("page"),
                        "excerpt": (recorded.get("excerpt") or "")[:200],
                    },
                )

            if (
                event_type == "tool.completed"
                and payload.get("tool_name") == "write_todos"
                and payload.get("items")
            ):
                # deepagents 内置 write_todos 工具 → plan.updated 事件（前端 Todo 渲染）
                await self._publisher.publish(
                    session_id=run.session_id,
                    run_id=run.run_id,
                    event_type="plan.updated",
                    actor="research-orchestrator",
                    payload={"items": payload["items"]},
                )

            if (
                event_type == "tool.completed"
                and payload.get("tool_name") == "submit_research_report"
            ):
                artifact_id = payload.get("artifact_id")
                degraded = payload.get("degraded", False)
                if artifact_id:
                    report_artifact_id = str(artifact_id)
                    await self._publish_report_ready(
                        run,
                        report_artifact_id,
                        degraded=degraded,
                        reason=payload.get("reason"),
                    )
                    if degraded:
                        return _StreamAttemptResult(
                            report_artifact_id=report_artifact_id,
                            degraded_reason=payload.get("reason"),
                        )

            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run.run_id,
                event_type=event_type,
                actor=actor,
                payload={k: v for k, v in payload.items() if k not in {"discovered", "recorded"}},
            )

            budget_reason = self._budget_reason(
                tool_call_count,
                time.monotonic() - started,
                context,
            )
            if budget_reason:
                return _StreamAttemptResult(
                    report_artifact_id=report_artifact_id,
                    budget_reason=budget_reason,
                )

        return _StreamAttemptResult(report_artifact_id=report_artifact_id)

    async def _prefetch_knowledge(self, context: RunContext, store: EvidenceStore) -> None:
        """Deterministically inject KB context + K evidence before model delegation."""
        if not self._settings.kb_prefetch_enabled or self._knowledge_index is None:
            return
        result = await search_knowledge_base_impl(
            query=context.question,
            knowledge_index=self._knowledge_index,
            top_k=self._settings.kb_prefetch_top_k,
        )
        lines: list[str] = []
        for rank, item in enumerate(result.get("results") or [], start=1):
            path = item.get("file_path") or ""
            if not path:
                continue
            excerpt = (item.get("text") or "")[:2000]
            title = item.get("symbol") or path
            line_start = int(item.get("line_start") or 0)
            line_end = int(item.get("line_end") or 0)
            try:
                recorded = await record_knowledge_base_evidence_impl(
                    context=context,
                    store=store,
                    path=path,
                    title=title,
                    excerpt=excerpt,
                    line_start=line_start,
                    line_end=line_end,
                )
            except Exception:  # noqa: BLE001 - stale index chunk should not block run
                continue
            await self._publisher.publish(
                session_id=context.session_id,
                run_id=context.run_id,
                event_type="source.discovered",
                actor="research-orchestrator",
                payload={
                    "evidence_id": recorded.get("evidence_id"),
                    "source_type": "knowledge_base",
                    "title": title,
                    "path": path,
                    "evidence_level": "first_party",
                },
            )
            await self._publisher.publish(
                session_id=context.session_id,
                run_id=context.run_id,
                event_type="evidence.recorded",
                actor="research-orchestrator",
                payload={
                    "evidence_id": recorded.get("evidence_id"),
                    "source_type": "knowledge_base",
                    "locator": recorded.get("locator"),
                    "line_start": line_start,
                    "line_end": line_end,
                    "excerpt": excerpt[:200],
                },
            )
            lines.append(
                f"[{rank}] {path}:{line_start}-{line_end} ({title})\n{excerpt[:600]}"
            )
        context.knowledge_context = "\n\n".join(lines)

    async def _publish_report_ready(
        self,
        run: Run,
        report_artifact_id: str,
        *,
        degraded: bool = False,
        reason: str | None = None,
    ) -> None:
        artifact_payload: dict = {
            "artifact_id": report_artifact_id,
            "artifact_kind": "report",
            "display_name": "research-report.md",
            "degraded": degraded,
        }
        if reason:
            artifact_payload["reason"] = reason
        await self._publisher.publish(
            session_id=run.session_id,
            run_id=run.run_id,
            event_type="artifact.created",
            payload=artifact_payload,
        )
        ready_payload: dict = {"artifact_id": report_artifact_id, "degraded": degraded}
        if reason:
            ready_payload["reason"] = reason
        await self._publisher.publish(
            session_id=run.session_id,
            run_id=run.run_id,
            event_type="report.ready",
            payload=ready_payload,
        )

    async def _write_degraded_report(
        self,
        context: RunContext,
        store: EvidenceStore,
        reason: str,
    ) -> str:
        report_data = {
            "title": f"[DEGRADED] {reason}",
            "executive_summary_claim_ids": ["degraded-summary"],
            "sections": [],
            "recommendations": [],
            "disagreements": [],
            "unknowns": [reason],
            "reason": reason,
        }
        result = await submit_research_report_impl(
            store=store,
            artifacts=self._artifacts,
            paths=self._paths,
            session_id=context.session_id,
            run_id=context.run_id,
            report_data=report_data,
        )
        return str(result["artifact_id"])

    def _resolve_budget(self, run: Run) -> tuple[int, float]:
        max_tool_calls = self._settings.agent_max_tool_calls
        max_elapsed_seconds = self._settings.agent_max_elapsed_seconds
        for constraint in run.request.constraints:
            stripped = constraint.strip()
            for sep in ("=", ":"):
                if sep not in stripped:
                    continue
                key, value = (part.strip() for part in stripped.split(sep, 1))
                if key == "max_tool_calls":
                    try:
                        max_tool_calls = max(0, int(value))
                    except ValueError:
                        pass
                elif key == "max_elapsed_seconds":
                    try:
                        max_elapsed_seconds = max(0.0, float(value))
                    except ValueError:
                        pass
        return max(0, int(max_tool_calls)), max(0.0, float(max_elapsed_seconds))

    def _budget_reason(
        self,
        tool_calls: int,
        elapsed_seconds: float,
        context: RunContext,
    ) -> str | None:
        if context.max_tool_calls and tool_calls >= context.max_tool_calls:
            return "budget_exceeded: max_tool_calls"
        if context.max_elapsed_seconds and elapsed_seconds >= context.max_elapsed_seconds:
            return "budget_exceeded: max_elapsed_seconds"
        return None
