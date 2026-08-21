from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from uuid import UUID

from langgraph.errors import GraphRecursionError

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.agents.model import (
    clone_for_structured_output,
    create_model_binding,
)
from ai_dev_researcher.agents.orchestrator import create_research_agent
from ai_dev_researcher.agents.stream_adapter import map_framework_event
from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.core.output_profiles import (
    RunBudget,
    get_output_profile,
    resolve_run_budget,
)
from ai_dev_researcher.domain.reports import ResearchReport
from ai_dev_researcher.domain.runs import ALLOWED_TRANSITIONS, Run, RunStatus
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.tools.knowledge_base import (
    KbToolBudget,
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
    recursion_error: str | None = None
    finalization_due: bool = False
    finalization_reason: str | None = None


# 空闲超时哨兵：事件流内连续 idle_timeout 秒无事件时由 _iter_with_idle_timeout 产出。
_IDLE_TIMEOUT_SENTINEL: dict = {"event": "__agent_idle_timeout__"}
_GRAPH_RECURSION_SENTINEL = object()

# record 类工具返回的短路 note：这些响应不产生真实证据，executor 不得发布
# source.discovered / evidence.recorded 账本事件（引导文案随 tool.completed 输出）。
_RECORD_BLOCKED_NOTES = {"budget_exceeded", "duplicate", "candidate_rejected"}

# 探索性工具（batch C）：受「max_tool_calls - reserve」探索预算约束。
# get_evidence_ledger / submit_research_report 为收尾工具，不占探索预算、不受其拦截。
_EXPLORATORY_TOOLS = frozenset(
    {
        "search_web",
        "extract_web_sources",
        "search_run_documents",
        "read_run_document",
        "list_run_documents",
        "record_document_evidence",
        "search_knowledge_base",
        "read_knowledge_base_file",
        "list_knowledge_base_entries",
        "record_knowledge_base_evidence",
    }
)


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


async def _iter_with_graph_recursion_classification(stream):
    """Convert only LangGraph recursion failures into an attempt result marker."""
    try:
        async for raw in stream:
            yield raw
    except GraphRecursionError as exc:
        yield (_GRAPH_RECURSION_SENTINEL, exc)


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

        run_started = time.monotonic()
        budget = self._resolve_budget(run)
        context = RunContext(
            run_id=run.run_id,
            session_id=run.session_id,
            question=run.request.question,
            uploaded_artifact_ids=run.request.uploaded_artifact_ids,
            max_web_sources=run.request.max_web_sources,
            constraints=run.request.constraints,
            focus_areas=run.request.focus_areas,
            output_mode=run.request.output_mode,
            paths=self._paths,
            settings=self._settings,
            max_tool_calls=budget.max_tool_calls,
            max_elapsed_seconds=budget.max_elapsed_seconds,
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
            profile = get_output_profile(run.request.output_mode)
            kb_budget = KbToolBudget(
                budget.kb_max_tool_calls,
                k_evidence_limit=profile.max_k_evidence,
            )
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
            ]

            last_message: str | None = None
            recursion_reasons: list[str] = []
            finalization_reasons: list[str] = []
            skip_remaining_attempts = False
            finalization_reserve = self._finalization_reserve(context)
            graph_deadline = (
                run_started + context.max_elapsed_seconds - finalization_reserve
                if context.max_elapsed_seconds > 0
                else None
            )
            for attempt_index, attempt in enumerate(attempts, start=1):
                if skip_remaining_attempts:
                    continue
                attempt_result = await self._run_agent_attempt(
                    run=run,
                    context=context,
                    store=store,
                    agent=agent,
                    input_payload=attempt["input"],
                    config=attempt["config"],
                    attempt_index=attempt_index,
                    exploration_budget=self._exploration_budget(budget),
                    run_started=run_started,
                    graph_deadline=graph_deadline,
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
                if attempt_result.recursion_error:
                    recursion_reasons.append(attempt_result.recursion_error)
                    skip_remaining_attempts = True
                    continue
                if attempt_result.finalization_due:
                    if attempt_result.finalization_reason:
                        finalization_reasons.append(attempt_result.finalization_reason)
                    skip_remaining_attempts = True
                    continue
            else:
                try:
                    submitted = await self._finalize_report_structured(
                        run=run,
                        context=context,
                        store=store,
                        model_binding=model_binding,
                        run_started=run_started,
                    )
                    report_artifact_id = str(submitted["artifact_id"])
                    if submitted.get("degraded"):
                        raise RuntimeError(
                            submitted.get("reason") or "submit returned degraded report"
                        )
                    await self._publish_report_ready(
                        run, report_artifact_id, degraded=False
                    )
                except _BudgetExceededError as exc:
                    reason = str(exc)
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
                    raise
                except Exception as exc:  # noqa: BLE001
                    finalization_context = "; ".join(
                        [*recursion_reasons, *finalization_reasons]
                    )
                    reason = (
                        f"{finalization_context}; structured finalization failed: {exc}"
                        if finalization_context
                        else f"structured finalization failed: {exc}"
                    )
                    if report_artifact_id is None:
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
                    else:
                        await self._publish_report_ready(
                            run,
                            report_artifact_id,
                            degraded=True,
                            reason=reason,
                        )
                    raise RuntimeError(reason) from exc

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
        exploration_budget: int | None = None,
        run_started: float | None = None,
        graph_deadline: float | None = None,
    ) -> _StreamAttemptResult:
        report_artifact_id: str | None = None
        last_message = ""
        # 维护 tool_call_id -> input_summary，用于在 tool.completed 上附带脱敏输入摘要。
        tool_inputs: dict[str, str] = {}
        attempt_started = time.monotonic()
        budget_started = run_started if run_started is not None else attempt_started
        stage_started = attempt_started
        stage = "plan"
        tool_call_count = 0
        # batch C：探索性工具调用计数，受 max_tool_calls - reserve 上限约束；
        # get_evidence_ledger / submit_research_report 不计入。
        exploration_count = 0
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
            return self._budget_reason(tool_call_count, now - budget_started, context)

        def _finalization_due_at(now: float) -> bool:
            return graph_deadline is not None and now >= graph_deadline

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
            elif (
                stage == "research"
                and event_type == "tool.completed"
                and payload.get("tool_name") == "get_evidence_ledger"
            ):
                stage = "report"
                stage_started = time.monotonic()

        classified_stream = _iter_with_graph_recursion_classification(
            _iter_with_idle_timeout(stream, idle_timeout)
        )
        async for raw in classified_stream:
            if (
                isinstance(raw, tuple)
                and len(raw) == 2
                and raw[0] is _GRAPH_RECURSION_SENTINEL
            ):
                recursion_error = raw[1]
                budget_reason = _budget_reason_at(time.monotonic())
                if budget_reason:
                    return _StreamAttemptResult(
                        budget_reason=budget_reason,
                        last_message=last_message or None,
                    )
                if _finalization_due_at(time.monotonic()):
                    return _StreamAttemptResult(
                        recursion_error=(
                            f"{type(recursion_error).__name__}: {recursion_error}"
                        ),
                        finalization_due=True,
                        last_message=last_message or None,
                    )
                return _StreamAttemptResult(
                    recursion_error=(
                        f"{type(recursion_error).__name__}: {recursion_error}"
                    ),
                    last_message=last_message or None,
                )
            if raw is _IDLE_TIMEOUT_SENTINEL:
                logger.warning(
                    "attempt %s idle timeout in stage=%s after %.1fs; treating as stuck",
                    attempt_index,
                    stage,
                    time.monotonic() - attempt_started,
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
                # batch C：探索达上限（max_tool_calls - reserve）后，新的探索类工具
                # 直接拦截返回预算原因（不执行、不 retry）；收尾工具不拦截。
                if payload.get("tool_name") in _EXPLORATORY_TOOLS:
                    if (
                        exploration_budget is not None
                        and exploration_count >= exploration_budget
                    ):
                        hard_budget_reason = _budget_reason_at(time.monotonic())
                        if hard_budget_reason:
                            return _StreamAttemptResult(
                                report_artifact_id=report_artifact_id,
                                budget_reason=hard_budget_reason,
                                last_message=last_message or None,
                            )
                        logger.warning(
                            "attempt %s exploration budget reached (count=%s>=%s); "
                            "blocking exploratory tool %s",
                            attempt_index,
                            exploration_count,
                            exploration_budget,
                            payload.get("tool_name"),
                        )
                        return _StreamAttemptResult(
                            report_artifact_id=report_artifact_id,
                            finalization_due=True,
                            finalization_reason="budget_exceeded: exploration_budget",
                            last_message=last_message or None,
                        )
                    exploration_count += 1
            if event_type == "tool.completed":
                payload["tool_input"] = tool_inputs.get(
                    payload.get("tool_call_id", ""), ""
                )

            if event_type == "tool.completed" and payload.get("discovered_list"):
                # #26/#E：search_web 可能一次返回多条结果，逐条发布账本事件，
                # 避免 last-wins 覆盖导致前端账本只记最后一条。
                for discovered in payload["discovered_list"]:
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
                # KB 软预算短路（#13）/ 重复候选（#A2 duplicate）/ 候选被拒（#A2）/
                # K 上限（#A2 budget_exceeded）：这些记录不产生真实新证据，
                # 不发布 source.discovered / evidence.recorded 脏账本事件；
                # 引导提示已随 tool.completed 的 output_summary 转发给前端。
                if recorded.get("note") not in _RECORD_BLOCKED_NOTES:
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
                    await self._publisher.publish(
                        session_id=run.session_id,
                        run_id=run.run_id,
                        event_type=event_type,
                        actor=actor,
                        payload={
                            k: v
                            for k, v in payload.items()
                            if k not in {"discovered", "discovered_list", "recorded"}
                        },
                    )
                    if degraded:
                        return _StreamAttemptResult(
                            report_artifact_id=report_artifact_id,
                            degraded_reason=payload.get("reason"),
                        )
                    return _StreamAttemptResult(
                        report_artifact_id=report_artifact_id,
                    )

            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run.run_id,
                event_type=event_type,
                actor=actor,
                payload={k: v for k, v in payload.items() if k not in {"discovered", "discovered_list", "recorded"}},
            )

            budget_reason = _budget_reason_at(time.monotonic())
            if budget_reason:
                return _StreamAttemptResult(
                    report_artifact_id=report_artifact_id,
                    budget_reason=budget_reason,
                )
            if _finalization_due_at(time.monotonic()) and not report_artifact_id:
                return _StreamAttemptResult(
                    finalization_due=True,
                    last_message=last_message or None,
                )

        budget_reason = _budget_reason_at(time.monotonic())
        if budget_reason:
            return _StreamAttemptResult(
                report_artifact_id=report_artifact_id,
                budget_reason=budget_reason,
                last_message=last_message or None,
            )
        if _finalization_due_at(time.monotonic()) and not report_artifact_id:
            return _StreamAttemptResult(
                finalization_due=True,
                last_message=last_message or None,
            )
        if not report_artifact_id:
            logger.warning(
                "attempt %s finished without submit_research_report: "
                "tool_calls=%s elapsed=%.1fs last_message=%s",
                attempt_index,
                tool_call_count,
                time.monotonic() - attempt_started,
                (last_message or "")[:1000],
            )
        return _StreamAttemptResult(
            report_artifact_id=report_artifact_id,
            last_message=last_message or None,
        )

    async def _finalize_report_structured(
        self,
        *,
        run: Run,
        context: RunContext,
        store: EvidenceStore,
        model_binding,
        run_started: float,
    ) -> dict:
        def _remaining_hard_budget() -> float | None:
            if context.max_elapsed_seconds <= 0:
                return None
            return context.max_elapsed_seconds - (time.monotonic() - run_started)

        remaining_total = _remaining_hard_budget()
        if remaining_total is not None and remaining_total <= 0:
            raise _BudgetExceededError("budget_exceeded: max_elapsed_seconds")
        try:
            ledger_summary = await asyncio.wait_for(
                self._evidence_ledger_summary(store),
                timeout=remaining_total,
            )
        except asyncio.TimeoutError as exc:
            raise _BudgetExceededError("budget_exceeded: max_elapsed_seconds") from exc
        structured_base_model = clone_for_structured_output(model_binding.instance)
        structured_model = structured_base_model.with_structured_output(
            ResearchReport,
            method="function_calling",
        )
        report_timeout = context.settings.agent_report_timeout_seconds
        remaining_total = _remaining_hard_budget()
        if remaining_total is not None:
            if remaining_total <= 0:
                raise _BudgetExceededError("budget_exceeded: max_elapsed_seconds")
        timeout_candidates = [
            value for value in (report_timeout, remaining_total) if value and value > 0
        ]
        limited_by_total = remaining_total is not None and (
            report_timeout <= 0 or remaining_total <= report_timeout
        )
        invoke = structured_model.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Generate a complete structured research report. "
                        "Use only evidence IDs present in the ledger. "
                        "Do not invent citations or evidence."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Research question:\n{run.request.question}\n\n"
                        f"Evidence ledger:\n{ledger_summary}"
                    ),
                },
            ]
        )
        try:
            result = await asyncio.wait_for(
                invoke,
                timeout=min(timeout_candidates) if timeout_candidates else None,
            )
        except asyncio.TimeoutError as exc:
            if limited_by_total:
                raise _BudgetExceededError(
                    "budget_exceeded: max_elapsed_seconds"
                ) from exc
            raise
        remaining_after_invoke = _remaining_hard_budget()
        if remaining_after_invoke is not None and remaining_after_invoke <= 0:
            raise _BudgetExceededError("budget_exceeded: max_elapsed_seconds")
        report = (
            result
            if isinstance(result, ResearchReport)
            else ResearchReport.model_validate(result)
        )
        submitted = await submit_research_report_impl(
            store=store,
            artifacts=self._artifacts,
            paths=self._paths,
            session_id=context.session_id,
            run_id=context.run_id,
            report_data=report.model_dump(mode="json"),
            system_generated=False,
        )
        if not submitted.get("artifact_id"):
            raise RuntimeError("structured report submission returned no artifact")
        return submitted

    async def _prefetch_knowledge(self, context: RunContext, store: EvidenceStore) -> None:
        """Deterministically inject KB context before model delegation (#13/#18).

        预取是启发式，其结果**不算确定性证据**（#18）：仅把高相关片段注入
        ``context.knowledge_context`` 供模型判断「问题是否与知识库主题相关」、
        进而决定是否委托 document-analyst。预取本身**不写证据账本、不发布
        source.discovered / evidence.recorded**；K 证据只能经模型显式调用
        ``record_knowledge_base_evidence`` 进入账本。

        ``store`` 形参保留以维持调用点与测试 fixture 签名稳定（B2 起不再使用）。
        """
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
        # #13/#18：检索层已按 score_threshold 过滤，这里再按 score 二次过滤，
        # 低于阈值的 chunk 视为与问题无关，不注入上下文。预取不落账本、不发事件。
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
            system_generated=True,
        )
        return str(result["artifact_id"])

    def _resolve_budget(self, run: Run) -> RunBudget:
        """Output-mode profile 初始值 + Settings 正数全局收紧 + constraints 更严格覆盖。

        Settings 正数作为收紧上限（min(profile, settings)）；0 不放开预算（不绕过
        profile，防成本失控）。KB 计数随 profile + settings 收紧（KB 软预算，record
        仍豁免）。预算超限后直接走 BUDGET_EXCEEDED 降级链，不继续搜索、不发起 retry。
        """
        return resolve_run_budget(
            run.request.output_mode, run.request.constraints, self._settings
        )

    @staticmethod
    def _exploration_budget(budget: RunBudget) -> int | None:
        """探索类工具预算 = max_tool_calls - reserve（为 ledger + submit 预留 2 次）。

        max_tool_calls <= 0 表示不限制（探索同样不限制，返回 None）；否则取
        max(0, ...)：当总预算不足以容纳 reserve 时，完全禁止探索，仅允许收尾工具。
        """
        if budget.max_tool_calls <= 0:
            return None
        return max(0, budget.max_tool_calls - budget.reserve)

    @staticmethod
    def _finalization_reserve(context: RunContext) -> float:
        """Reserve one third of the hard run budget for structured finalization."""
        if context.max_elapsed_seconds <= 0:
            return 0.0
        report_timeout = context.settings.agent_report_timeout_seconds
        if report_timeout > 0:
            return min(context.max_elapsed_seconds / 3, report_timeout)
        return context.max_elapsed_seconds / 3

    def _budget_reason(
        self,
        tool_calls: int,
        elapsed_seconds: float,
        context: RunContext,
    ) -> str | None:
        # 工具调用预算已由「探索预算」承担（batch C：在探索工具 tool.started 时拦截），
        # 此处只保留耗时看门狗；收尾工具（get_evidence_ledger / submit）不受其拦截。
        if context.max_elapsed_seconds and elapsed_seconds >= context.max_elapsed_seconds:
            return "budget_exceeded: max_elapsed_seconds"
        return None
