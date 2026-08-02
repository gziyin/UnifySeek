"""M1 诊断：用 ainvoke 看真实 Agent 是否调用工具。"""

from __future__ import annotations

import asyncio
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


async def main() -> int:
    settings = Settings(
        workspace_root=backend_root / "workspace" / "m1-diagnose",
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

    # Create stub session/run records to satisfy foreign keys for report artifact.
    from datetime import datetime

    now_iso = datetime.utcnow().isoformat()
    await conn.execute(
        "INSERT INTO sessions (session_id, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (str(session_id), "active", now_iso, now_iso),
    )
    await conn.execute(
        "INSERT INTO runs (run_id, session_id, status, request_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            str(run_id),
            str(session_id),
            "running",
            '{"question": "M1 diagnose"}',
            now_iso,
        ),
    )
    await conn.commit()

    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="调研 DeepAgents 与 LangGraph 在 AI Agent 编排上的主要差异，重点比较子智能体委派方式、状态持久化、工具调用机制。",
        uploaded_artifact_ids=[],
        max_web_sources=5,
        constraints=["只使用公开网页来源"],
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

    input_payload = {"messages": [{"role": "user", "content": context.question}]}
    config = {"configurable": {"thread_id": str(run_id)}}

    print(f"Running ainvoke with {binding.spec} ...")
    try:
        result = await agent.ainvoke(input_payload, config=config)
        print("\n--- Result keys ---")
        print(list(result.keys()) if isinstance(result, dict) else type(result))
        if isinstance(result, dict) and "messages" in result:
            messages = result["messages"]
            print(f"\n--- Messages ({len(messages)}) ---")
            for i, msg in enumerate(messages):
                print(f"[{i}] {type(msg).__name__}: {msg}")
        else:
            print(result)
    except Exception as exc:
        print("ERROR during ainvoke:")
        traceback.print_exc()

    await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
