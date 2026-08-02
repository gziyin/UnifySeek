"""M1 诊断：完整消费 astream_events，打印事件结构样本，找到 on_tool_end 的表示方式。"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
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


async def main() -> int:
    settings = Settings(
        workspace_root=backend_root / "workspace" / "m1-dump-events",
        fake_agent_mode=False,
    )
    if not settings.deepseek_api_key or not settings.tavily_api_key:
        print("ERROR: keys required")
        return 1

    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    conn = await connect(str(settings.db_path))
    await init_db(conn)

    paths = WorkspacePaths(settings.sessions_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)

    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="调研 DeepAgents 与 LangGraph 的差异，重点比较子智能体委派方式。",
        uploaded_artifact_ids=[],
        max_web_sources=3,
        constraints=["只使用公开网页来源"],
        focus_areas=["子智能体委派方式"],
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

    input_payload = {"messages": [{"role": "user", "content": context.question}]}
    config = {"configurable": {"thread_id": str(run_id)}}

    print(f"Running astream_events with {binding.spec} ...")
    stream = agent.astream_events(input_payload, config=config, version="v3")
    if inspect.isawaitable(stream):
        stream = await stream

    events: list[object] = []
    async for raw in stream:
        events.append(raw)

    print(f"Total events: {len(events)}")

    # Dump all events.
    dump_path = paths.run_dir(session_id, run_id) / "all_events.jsonl"
    with dump_path.open("w", encoding="utf-8") as f:
        for raw in events:
            try:
                f.write(json.dumps(raw, default=str, ensure_ascii=False) + "\n")
            except Exception:
                f.write(json.dumps({"unserializable": str(raw)}, ensure_ascii=False) + "\n")
    print(f"Dumped to {dump_path}")

    # Print samples of unique structures.
    structures: dict[str, int] = {}
    samples: dict[str, object] = {}
    for raw in events:
        key = type(raw).__name__
        if isinstance(raw, dict):
            key = f"dict:{','.join(sorted(raw.keys()))}"
        structures[key] = structures.get(key, 0) + 1
        if key not in samples:
            samples[key] = raw

    print("\n--- Event structures ---")
    for key, count in sorted(structures.items(), key=lambda x: -x[1]):
        print(f"  {count:4d} {key}")

    print("\n--- First sample per structure ---")
    for key, sample in samples.items():
        print(f"\n{key}:")
        try:
            print(json.dumps(sample, default=str, ensure_ascii=False, indent=2)[:1200])
        except Exception:
            print(str(sample)[:1200])

    # Find tool-related events.
    print("\n--- Tool-related events ---")
    for raw in events:
        text = json.dumps(raw, default=str, ensure_ascii=False)
        if "tool" in text.lower() or "submit_research_report" in text:
            print(text[:800])
            print("---")

    await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
