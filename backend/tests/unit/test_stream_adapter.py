from __future__ import annotations

from ai_dev_researcher.agents.stream_adapter import map_framework_event


def _tool_start(name: str, run_id: str, input_: dict) -> dict:
    return {"event": "on_tool_start", "name": name, "run_id": run_id, "data": {"input": input_}}


def _tool_end(name: str, run_id: str, output) -> dict:
    return {"event": "on_tool_end", "name": name, "run_id": run_id, "data": {"output": output}}


def test_map_tool_start_v2():
    event_type, actor, payload = map_framework_event(_tool_start("search_web", "r1", {"query": "x"}))
    assert event_type == "tool.started"
    assert payload["tool_name"] == "search_web"
    assert payload["tool_call_id"] == "r1"
    assert "query" in payload["input_summary"]


def test_map_search_web_end_extracts_discovered():
    output = {"items": [{"evidence_id": "S1", "title": "T", "url": "https://x", "evidence_level": "search_snippet"}]}
    event_type, _, payload = map_framework_event(_tool_end("search_web", "r1", output))
    assert event_type == "tool.completed"
    assert payload["tool_name"] == "search_web"
    assert payload["discovered"]["evidence_id"] == "S1"


def test_map_submit_report_end_extracts_artifact_id():
    output = {"artifact_id": "abc-123", "title": "t"}
    event_type, _, payload = map_framework_event(_tool_end("submit_research_report", "r1", output))
    assert event_type == "tool.completed"
    assert payload["artifact_id"] == "abc-123"
    assert payload["degraded"] is False


def test_map_submit_report_degraded():
    output = {"artifact_id": "abc-123", "degraded": True, "reason": "bad citations"}
    _, _, payload = map_framework_event(_tool_end("submit_research_report", "r1", output))
    assert payload["degraded"] is True
    assert payload["reason"] == "bad citations"


def test_map_task_chain_start_reads_subagent_from_input():
    # v2 协议下 subagent 名位于 data.input（task 工具参数），不在 data 顶层。
    raw = {
        "event": "on_chain_start",
        "name": "task",
        "run_id": "t1",
        "data": {"input": {"subagent": "web-researcher", "prompt": "search DeepAgents"}},
    }
    event_type, actor, payload = map_framework_event(raw)
    assert event_type == "agent.started"
    assert actor == "research-orchestrator"
    assert payload["agent_name"] == "web-researcher"


def test_map_unknown_event_returns_none():
    event_type, actor, payload = map_framework_event({"event": "on_chat_model_stream", "name": "ChatDeepSeek", "data": {}})
    assert event_type is None
    assert actor == "system"
    assert payload == {}
