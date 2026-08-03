from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

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


def test_map_submit_report_end_with_toolmessage_output():
    """langchain 1.x ToolNode 的 on_tool_end data.output 是 ToolMessage 而非 dict。"""
    output = ToolMessage(
        content=json.dumps({"artifact_id": "abc-123", "title": "t", "degraded": False, "reason": None}),
        tool_call_id="r1",
        name="submit_research_report",
    )
    raw = {"event": "on_tool_end", "name": "submit_research_report", "run_id": "r1", "data": {"output": output}}
    event_type, _, payload = map_framework_event(raw)
    assert event_type == "tool.completed"
    assert payload["artifact_id"] == "abc-123"


def test_map_search_web_end_with_toolmessage_output():
    output = ToolMessage(
        content=json.dumps(
            {"items": [{"evidence_id": "S1", "title": "T", "url": "https://x", "evidence_level": "search_snippet"}]}
        ),
        tool_call_id="r1",
        name="search_web",
    )
    raw = {"event": "on_tool_end", "name": "search_web", "run_id": "r1", "data": {"output": output}}
    _, _, payload = map_framework_event(raw)
    assert payload["discovered"]["evidence_id"] == "S1"


def test_map_tool_end_with_unparseable_output_keeps_summary_only():
    raw = {"event": "on_tool_end", "name": "search_web", "run_id": "r1", "data": {"output": "some plain error text"}}
    _, _, payload = map_framework_event(raw)
    assert payload["tool_name"] == "search_web"
    assert "discovered" not in payload


def test_map_unknown_event_returns_none():
    event_type, actor, payload = map_framework_event({"event": "on_chat_model_stream", "name": "ChatDeepSeek", "data": {}})
    assert event_type is None
    assert actor == "system"
    assert payload == {}


def test_map_search_web_end_includes_metadata_fields():
    output = {
        "items": [
            {
                "evidence_id": "S1",
                "title": "T",
                "url": "https://example.com/a",
                "query": "deepagents",
                "publisher_key": "example.com",
                "result_rank": 1,
                "evidence_level": "search_snippet",
            }
        ]
    }
    _, _, payload = map_framework_event(_tool_end("search_web", "r1", output))
    discovered = payload["discovered"]
    assert discovered["url"] == "https://example.com/a"
    assert discovered["query"] == "deepagents"
    assert discovered["publisher_key"] == "example.com"


def test_map_record_knowledge_base_evidence_extracts_recorded():
    output = {
        "evidence_id": "K1",
        "locator": "kb:deepagents-0.6.2/README.md lines 1-3",
        "path": "deepagents-0.6.2/README.md",
        "line_start": 1,
        "line_end": 3,
        "excerpt": "excerpt…",
    }
    _, _, payload = map_framework_event(_tool_end("record_knowledge_base_evidence", "r1", output))
    assert payload["tool_name"] == "record_knowledge_base_evidence"
    assert payload["recorded"]["evidence_id"] == "K1"
    assert payload["recorded"]["path"] == "deepagents-0.6.2/README.md"


def test_map_record_document_evidence_includes_artifact_meta():
    output = {
        "evidence_id": "D1",
        "artifact_id": "art-1",
        "display_name": "notes.md",
        "locator": "lines 1-3",
        "line_start": 1,
        "line_end": 3,
        "page": 2,
        "excerpt": "note…",
    }
    _, _, payload = map_framework_event(_tool_end("record_document_evidence", "r1", output))
    recorded = payload["recorded"]
    assert recorded["artifact_id"] == "art-1"
    assert recorded["display_name"] == "notes.md"
    assert recorded["line_start"] == 1
    assert recorded["page"] == 2


def test_map_write_todos_extracts_items():
    output = {"todos": [{"id": "1", "content": "搜索网页", "status": "in_progress"}]}
    _, _, payload = map_framework_event(_tool_end("write_todos", "r1", output))
    assert payload["tool_name"] == "write_todos"
    assert payload["items"][0]["content"] == "搜索网页"
