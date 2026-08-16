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
    KbToolBudget,
    record_knowledge_base_evidence_impl,
    search_knowledge_base_impl,
)
from ai_dev_researcher.tools.report_submitter import submit_research_report_impl

logger = logging.getLogger(__name__)


def _message_text(value) -> str:
    """Extract text from an on_chat_model_end output (AIMessage or raw dict)."""
    content = getattr(value, "content", None)
    if content is None and isinstance(value, dict):
        content = value.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(value) if value is not None else ""


class _BudgetExceededError(RuntimeError):
    """Marker so the executor can attribute a run failure to budget controls."""


@dataclass
class _StreamAttemptResult:
    report_artifact_id: str | None = None
    budget_reason: str | None = None
    degraded_reason: str | None = None
    last_message: str | None = None


# 空闲超时哨兵：事件流内连续 idle_timeout 秒无事件时由 _iter_with_idle_timeout 产出。
_IDLE_TIMEOUT_SENTINEL: dict = {"event": "__agent_idle_timeout__"}


def _iter_with_idle_timeout(stream, timeout: float | None):
    """Wrap an async event stream with a per-event idle timeout (#40).

    事件流内若连续 ``timeout`` 秒无事件（模型/工具调用卡住），产出
    ``_IDLE_TIMEOUT_SENTINEL`` 并结束；否则原样转发事件。超时/提前退出/取消时
    在 finally 中关闭底层流（带 2s 兜底，避免 aclose 自身挂死）。
    """
    iterator = stream.__aiter__()

    async def _gen():
        try:
            while True:
                try:
                    if timeout:
                        yield await asyncio.wait_for(anext(iterator), timeout=timeout)
                    else:
                        yield await anext(iterator)
                except StopAsyncIteration:
                    return
                except asyncio.TimeoutError:
                    yield _IDLE_TIMEOUT_SENTINEL
                    return
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                try:
                    await asyncio.wait_for(close(), timeout=2)
                except Exception:  # noqa: BLE001 - aclose 失败/超时不影响收敛
                    pass

    return _gen()


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
            kb_budget = KbToolBudget(self._settings.kb_max_tool_calls)
            agent = create_research_agent(
                context,
                model_binding,
                store,
                self._artifacts,
                vector_store=self._vector_store,
                knowledge_index=self._knowledge_index,
                kb_budget=kb_budget,
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

            attempts = [
                {
                    "input": input_payload,
                    "config": config,
                },
                {
                    "input": {
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
                    },
                    "config": {"configurable": {"thread_id": f"{run_id}:retry"}},
                },
                None,
            ]

            last_message: str | None = None
            for attempt_index, attempt in enumerate(attempts, start=1):
                if attempt_index == 3:
                    ledger_summary = await self._evidence_ledger_summary(store)
                    attempt = {
                        "input": {
                            "messages": [
                                {"role": "user", "content": run.request.question},
                                {
                                    "role": "user",
                                    "content": (
                                        "前两轮均已结束但未提交研究报告。下面是当前证据账本中"
                                        "可直接引用的证据（只读摘要，最多 20 条）：\n"
                                        f"{ledger_summary}\n\n"
                                        "硬性要求：禁止再调用搜索/提取类工具；禁止输出纯文本报告；"
                                        "必须立即调用 submit_research_report 提交报告；"
                                        "每条 claim 只能引用上面列出的真实证据 ID；"
                                        "若证据不足，请使用 medium/low 置信度并写入 unknowns，"
                                        "但仍必须调用 submit_research_report。"
                                    ),
                                },
                            ]
                        },
                        "config": {"configurable": {"thread_id": f"{run_id}:retry2"}},
                    }
                attempt_result = await self._run_agent_attempt(
                    run=run,
                    context=context,
                    store=store,
                    agent=agent,
                    input_payload=attempt["input"],
                    config=attempt["config"],
                    attempt_index=attempt_index,
                )
                if attempt_result.last_message:
                    last_message = attempt_result.last_message

                if attempt_result.report_artifact_id and attempt_result.degraded_reason:
                    report_artifact_id = attempt_result.report_artifact_id
                    raise RuntimeError(
                        f"submit_research_report returned degraded report: {attempt_result.degraded_reason}"
                    )
                if attempt_result.report_artifact_id:
                    report_artifact_id = attempt_result.report_artifact_id
                    break
                if attempt_result.budget_reason:
                    report_artifact_id = await self._write_degraded_report(
                        context, store, attempt_result.budget_reason
                    )
                    await self._publish_report_ready(
                        run,
                        report_artifact_id,
                        degraded=True,
                        reason=attempt_result.budget_reason,
                    )
                    raise _BudgetExceededError(attempt_result.budget_reason)
            else:
                reason = (
                    "agent finished without submit_research_report after two controlled retries"
                )
                if last_message:
                    reason += f"; last assistant message: {last_message[:500]}"
                report_artifact_id = await self._write_degraded_report(
                    context,
                    store,
                    reason,
                    last_message=last_message,
                )
                await self._publish_report_ready(
                    run,
                    report_artifact_id,
                    degraded=True,
                    reason=reason,
                )
                raise RuntimeError(reason)

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
            # 收敛自身异常不得逃逸（否则 task 失败且 run 永久 active，由 stale 回收器兜底）。
            try:
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
            except Exception:  # noqa: BLE001
                logger.exception("failed to converge cancelled run %s", run_id)
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
        # 收敛路径自身异常不得逃逸：否则 task 失败且 run 行永久停留 active，
        # session 被 409 锁死（#40）。异常只记录，终态由 stale 回收器兜底收敛。
        try:
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
        except Exception:  # noqa: BLE001
            logger.exception("failed to converge run %s to FAILED(%s)", run_id, error_code)
            return False

    async def _run_agent_attempt(
        self,
        *,
        run: Run,
        context: RunContext,
        store: EvidenceStore,
        agent,
        input_payload: dict,
        config: dict,
        attempt_index: int = 1,
    ) -> _StreamAttemptResult:
        report_artifact_id: str | None = None
        last_message = ""
        # 维护 tool_call_id -> input_summary，用于在 tool.completed 上附带脱敏输入摘要。
        tool_inputs: dict[str, str] = {}
        started = time.monotonic()
        stage_started = started
        stage = "plan"
        tool_call_count = 0
        # 使用 v2 经典事件协议（on_tool_start/on_tool_end，{event,name,data,run_id}）。
        # langgraph 1.2.10 的 v3 是实验性 run-stream 协议（{type,method,params}），
        # 与 stream_adapter.map_framework_event 的解析格式不兼容（M1 实测发现）。
        stream = agent.astream_events(input_payload, config=config, version="v2")
        if inspect.isawaitable(stream):
            stream = await stream
        idle_timeout = context.settings.agent_idle_timeout_seconds or None

        def _stage_budget() -> float | None:
            if stage == "plan":
                value = context.settings.agent_plan_timeout_seconds
            elif stage == "research":
                value = context.settings.agent_research_timeout_seconds
            else:
                value = context.settings.agent_report_timeout_seconds
            return value or None

        def _budget_reason_at(now: float) -> str | None:
            # 阶段级看门狗：plan/research/report 各自独立预算，避免慢但正常的
            # 单阶段误触发；总时长仍受 _budget_reason 的 max_elapsed 约束（#40）。
            sb = _stage_budget()
            if sb and now - stage_started >= sb:
                return f"budget_exceeded: stage_timeout:{stage}"
            return self._budget_reason(tool_call_count, now - started, context)

        def _advance_stage(event_type: str, payload: dict) -> None:
            nonlocal stage, stage_started
            if stage == "plan" and (
                event_type == "plan.updated" or event_type == "tool.started"
            ):
                stage = "research"
                stage_started = time.monotonic()
            elif (
                stage == "research"
                and event_type == "tool.started"
                and payload.get("tool_name") == "submit_research_report"
            ):
                stage = "report"
                stage_started = time.monotonic()

        async for raw in _iter_with_idle_timeout(stream, idle_timeout):
            if raw is _IDLE_TIMEOUT_SENTINEL:
                logger.warning(
                    "attempt %s idle timeout in stage=%s after %.1fs; treating as stuck",
                    attempt_index,
                    stage,
                    time.monotonic() - started,
                )
                return _StreamAttemptResult(
                    report_artifact_id=report_artifact_id,
                    budget_reason=f"budget_exceeded: idle_timeout:{stage}",
                    last_message=last_message or None,
                )
            if not isinstance(raw, dict):
                continue
            if raw.get("event") == "on_chat_model_end":
                output = (raw.get("data") or {}).get("output")
                content = _message_text(output)
                if content.strip():
                    last_message = content
            event_type, actor, payload = map_framework_event(raw)
            if event_type is None:
                continue
            _advance_stage(event_type, payload)

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
                # KB 软预算短路（#13）：record 被预算拦下时不产生真实证据，
                # 不发布 source.discovered / evidence.recorded 脏账本事件；
                # 引导提示已随 tool.completed 的 output_summary 转发给前端。
                if recorded.get("note") != "budget_exceeded":
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

            budget_reason = _budget_reason_at(time.monotonic())
            if budget_reason:
                return _StreamAttemptResult(
                    report_artifact_id=report_artifact_id,
                    budget_reason=budget_reason,
                )

        if not report_artifact_id:
            logger.warning(
                "attempt %s finished without submit_research_report: "
                "tool_calls=%s elapsed=%.1fs last_message=%s",
                attempt_index,
                tool_call_count,
                time.monotonic() - started,
                (last_message or "")[:1000],
            )
        return _StreamAttemptResult(
            report_artifact_id=report_artifact_id,
            last_message=last_message or None,
        )

    async def _prefetch_knowledge(self, context: RunContext, store: EvidenceStore) -> None:
        """Deterministically inject KB context + K evidence before model delegation."""
        if not self._settings.kb_prefetch_enabled or self._knowledge_index is None:
            return
        # 超时保护：embedding 加载/检索慢或失败时（如离线缓存缺失走在线 HF 路径会
        # 5 次指数退避重试、拖垮 run 启动），超时即跳过 prefetch，不阻塞研究进行。
        try:
            result = await asyncio.wait_for(
                search_knowledge_base_impl(
                    query=context.question,
                    knowledge_index=self._knowledge_index,
                    top_k=self._settings.kb_prefetch_top_k,
                    score_threshold=self._settings.kb_prefetch_score_threshold,
                ),
                timeout=self._settings.kb_prefetch_timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "KB prefetch timed out after %ss for run %s; skipping prefetch",
                self._settings.kb_prefetch_timeout_seconds,
                context.run_id,
            )
            return
        # 双保险：#13/#18——检索层已按 score_threshold 过滤，这里再按 score 二次过滤，
        # 低于阈值的 chunk 视为与问题无关，不记录证据、不发布账本事件。
        score_threshold = self._settings.kb_prefetch_score_threshold
        lines: list[str] = []
        for rank, item in enumerate(result.get("results") or [], start=1):
            path = item.get("file_path") or ""
            if not path:
                continue
            if (item.get("score") or 0.0) < score_threshold:
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

    async def _evidence_ledger_summary(
        self,
        store: EvidenceStore,
        *,
        limit: int = 20,
        excerpt_limit: int = 240,
    ) -> str:
        records = await store.list_for_run()
        lines: list[str] = []
        for record in records[:limit]:
            excerpt = store.excerpt(record, limit=excerpt_limit)
            lines.append(
                f"- {record.id} | {record.source_type} | {record.evidence_level} | "
                f"{record.title} | {record.locator} | {excerpt}"
            )
        if not lines:
            return "- 当前证据账本为空"
        return "\n".join(lines)

    async def _write_degraded_report(
        self,
        context: RunContext,
        store: EvidenceStore,
        reason: str,
        last_message: str | None = None,
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
        if last_message:
            report_data["model_final_message"] = last_message
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
