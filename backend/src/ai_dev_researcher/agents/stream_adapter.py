from __future__ import annotations

import json
from typing import Any

from ai_dev_researcher.domain.events import EventType


def _safe_summary(value: Any, *, limit: int = 180) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _coerce_tool_output(output: Any) -> dict | None:
    """规约 on_tool_end 的 data.output 为 dict。

    langchain 1.x ToolNode 的 on_tool_end data.output 可能是 ToolMessage
    （content 为 JSON 字符串、name 为工具名），而非工具函数的原始返回 dict。
    统一尝试规约：dict 直接用；对象取其 .content；字符串直接 json.loads。
    规约失败返回 None（调用方按"拿不到结构化结果"处理）。
    """
    if isinstance(output, dict):
        return output
    content = getattr(output, "content", None)
    if content is None and isinstance(output, str):
        content = output
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


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
        result = _coerce_tool_output(output)
        if tool_name == "search_web" and result is not None:
            for item in result.get("items", []):
                if isinstance(item, dict) and item.get("evidence_id"):
                    payload["discovered"] = item
        if tool_name in {"record_document_evidence", "record_knowledge_base_evidence"} and result is not None:
            payload["recorded"] = result
        if tool_name == "submit_research_report" and result is not None:
            payload["artifact_id"] = result.get("artifact_id")
            payload["degraded"] = result.get("degraded", False)
            payload["reason"] = result.get("reason")
        if tool_name == "write_todos" and result is not None:
            payload["items"] = result.get("todos", []) or result.get("items", [])
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
