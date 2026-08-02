"""阶段三 M1 真实闭环冒烟测试。

直接驱动真实 DeepSeek + Tavily Agent，验证“问题 -> 报告”端到端闭环。
预期会暴露 DeepSeek 行为可控性、submit_research_report schema 适配、
stream_adapter 事件解析等问题，是 M1 最关键的调试脚本。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import traceback
from pathlib import Path
from uuid import uuid4

backend_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_root / "src"))

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.agents.model import create_model_binding
from ai_dev_researcher.agents.orchestrator import create_research_agent
from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.paths import WorkspacePaths


MAX_EVENTS = 120


async def main() -> int:
    settings = Settings(
        workspace_root=backend_root / "workspace" / "m1-smoke",
        fake_agent_mode=False,
    )
    if not settings.deepseek_api_key or not settings.tavily_api_key:
        print("ERROR: DEEPSEEK_API_KEY and TAVILY_API_KEY are required (read from .env)")
        return 1

    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await connect(str(db_path))
    await init_db(conn)

    paths = WorkspacePaths(settings.sessions_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)

    question = (
        "调研 DeepAgents 与 LangGraph 在 AI Agent 编排上的主要差异，"
        "重点比较：子智能体委派方式、状态持久化、工具调用机制。"
        "请使用 3-5 个网页来源，最后通过 submit_research_report 提交一份结构化 Markdown 报告。"
    )

    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question=question,
        uploaded_artifact_ids=[],
        max_web_sources=5,
        constraints=["只使用公开网页来源", "每个事实性结论必须带证据引用"],
        focus_areas=["子智能体委派方式", "状态持久化", "工具调用机制"],
        paths=paths,
        settings=settings,
    )

    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    artifacts = ArtifactRepository(conn)

    binding = create_model_binding(settings)
    agent = create_research_agent(context, binding, store, artifacts)

    input_payload = {"messages": [{"role": "user", "content": question}]}
    config = {"configurable": {"thread_id": str(run_id)}}

    events: list[object] = []
    report_artifact_id: str | None = None
    last_error: Exception | None = None

    print(f"Starting M1 smoke: session={session_id} run={run_id}")
    print(f"Model: {binding.spec}")
    print(f"Question: {question}\n")

    try:
        stream = agent.astream_events(input_payload, config=config, version="v3")
        if inspect.isawaitable(stream):
            stream = await stream

        async for raw in stream:
            events.append(raw)
            if len(events) >= MAX_EVENTS:
                print(f"WARNING: reached max_events={MAX_EVENTS}, stopping stream")
                break

            if isinstance(raw, dict):
                event_name = str(raw.get("event", ""))
                name = str(raw.get("name", ""))
                data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
                if event_name == "on_tool_end" and name == "submit_research_report":
                    output = data.get("output")
                    if isinstance(output, dict):
                        report_artifact_id = output.get("artifact_id")
                        print(f"SUBMIT SUCCESS: artifact_id={report_artifact_id}")
    except Exception as exc:
        last_error = exc
        traceback.print_exc()

    print(f"\n--- Event summary ({len(events)} events) ---")
    tool_events: list[tuple[str, str]] = []
    for raw in events:
        if not isinstance(raw, dict):
            continue
        ev = raw.get("event")
        nm = raw.get("name")
        if ev in ("on_tool_start", "on_tool_end", "on_tool_error"):
            tool_events.append((str(ev), str(nm)))
        if ev == "on_chain_start" and nm == "task":
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            sub = data.get("subagent", "unknown")
            inp = data.get("input", "")
            print(f"DELEGATION: subagent={sub} input={str(inp)[:100]}")

    for ev, nm in tool_events:
        print(f"  {ev}: {nm}")

    if report_artifact_id:
        from uuid import UUID

        artifact = await artifacts.get(UUID(report_artifact_id))
        if artifact:
            report_path = Path(artifact.original_storage_path)
            print(f"\n--- Report path ---\n{report_path}")
            if report_path.exists():
                content = report_path.read_text(encoding="utf-8")
                print(f"\n--- Report markdown ({len(content)} chars) ---\n")
                print(content)
            else:
                print("Report file not found on disk")
        else:
            print("Artifact not found in DB")
    else:
        print("\n--- No report submitted ---")
        if last_error:
            print(f"Last error: {last_error}")

    # Dump raw events for offline debugging.
    dump_path = paths.run_dir(session_id, run_id) / "m1_events.jsonl"
    with dump_path.open("w", encoding="utf-8") as f:
        for raw in events:
            try:
                f.write(json.dumps(raw, default=str, ensure_ascii=False) + "\n")
            except Exception:
                f.write(json.dumps({"unserializable": str(raw)}, ensure_ascii=False) + "\n")
    print(f"\nRaw events dumped to: {dump_path}")

    await conn.close()
    return 0 if report_artifact_id else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
