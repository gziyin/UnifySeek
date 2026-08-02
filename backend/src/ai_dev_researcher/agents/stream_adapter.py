from __future__ import annotations

from typing import Any

from ai_dev_researcher.domain.events import EventType


def _safe_summary(value: Any, *, limit: int = 180) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def map_framework_event(raw: dict[str, Any]) -> tuple[EventType | None, str, dict[str, Any]]:
    """Map a DeepAgents/LangGraph v2 event to a domain event.

    v2 经典协议事件形如 {"event": "on_tool_end", "name": ..., "data": {...}, "run_id": ...}。
    langgraph 1.2.10 的 v3 实验性 run-stream 协议（{type,method,params}）与此不兼容，
    调用方（AgentResearchExecutor）统一使用 version="v2"。
    """
    event_name = str(raw.get("event", ""))
    name = str(raw.get("name", ""))
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}

    if event_name == "on_tool_start":
        tool_name = name or str(data.get("name", "tool"))
        return (
            "tool.started",
            "system",
            {
                "tool_name": tool_name,
                "tool_call_id": str(raw.get("run_id", "")),
                "input_summary": _safe_summary(data.get("input", {})),
            },
        )

    if event_name == "on_tool_end":
        tool_name = name or str(data.get("name", "tool"))
        output = data.get("output")
        payload: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_call_id": str(raw.get("run_id", "")),
            "output_summary": _safe_summary(output),
        }
        if tool_name == "search_web" and isinstance(output, dict):
            for item in output.get("items", []):
                if isinstance(item, dict) and item.get("evidence_id"):
                    payload["discovered"] = item
        if tool_name == "record_document_evidence" and isinstance(output, dict):
            payload["recorded"] = output
        if tool_name == "submit_research_report" and isinstance(output, dict):
            payload["artifact_id"] = output.get("artifact_id")
            payload["degraded"] = output.get("degraded", False)
            payload["reason"] = output.get("reason")
        return ("tool.completed", "system", payload)

    if event_name == "on_tool_error":
        tool_name = name or str(data.get("name", "tool"))
        return (
            "tool.failed",
            "system",
            {
                "tool_name": tool_name,
                "tool_call_id": str(raw.get("run_id", "")),
                "code": "TOOL_ERROR",
                "message": _safe_summary(data.get("error", "tool failed")),
            },
        )

    if event_name == "on_chain_start" and name == "task":
        # v2 协议下 subagent 名在 data.input 中（task 工具参数），不在 data 顶层。
        task_input = data.get("input")
        task_args = task_input if isinstance(task_input, dict) else {}
        return (
            "agent.started",
            "research-orchestrator",
            {
                "agent_name": str(task_args.get("subagent", "subagent")),
                "task_id": str(raw.get("run_id", "")),
                "description": _safe_summary(task_input),
            },
        )

    if event_name == "on_chain_end" and name == "task":
        return (
            "agent.completed",
            "research-orchestrator",
            {
                "agent_name": str(data.get("subagent", "subagent")),
                "task_id": str(raw.get("run_id", "")),
                "summary": _safe_summary(data.get("output", "")),
            },
        )

    return None, "system", {}
