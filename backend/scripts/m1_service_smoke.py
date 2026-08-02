"""阶段三 M1 真实闭环服务层冒烟测试。

走 SessionService -> RunService -> TaskManager -> AgentResearchExecutor 完整链路，
验证真实 DeepSeek+Tavily 能生成带引用 Markdown 报告。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

backend_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_root / "src"))

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.domain.runs import ResearchRequest, RunStatus
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.executor_factory import create_run_executor
from ai_dev_researcher.services.run_service import RunService
from ai_dev_researcher.services.session_service import SessionService
from ai_dev_researcher.services.task_manager import TaskManager
from ai_dev_researcher.storage.paths import WorkspacePaths


async def main() -> int:
    settings = Settings(
        workspace_root=backend_root / "workspace" / "m1-service-smoke",
        fake_agent_mode=False,
    )
    if not settings.deepseek_api_key or not settings.tavily_api_key:
        print("ERROR: DEEPSEEK_API_KEY and TAVILY_API_KEY required (read from .env)")
        return 1

    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await connect(str(db_path))
    await init_db(conn)

    paths = WorkspacePaths(settings.sessions_root)
    events_repo = EventRepository(conn)
    publisher = EventPublisher(events_repo, queue_size=settings.ws_send_queue_size)
    sessions_repo = SessionRepository(conn)
    runs_repo = RunRepository(conn)
    artifacts_repo = ArtifactRepository(conn)
    evidence_repo = EvidenceRepository(conn)

    session_service = SessionService(sessions_repo, paths)
    task_manager = TaskManager(
        executor_factory=lambda: create_run_executor(
            settings=settings,
            runs=runs_repo,
            artifacts=artifacts_repo,
            evidence=evidence_repo,
            publisher=publisher,
            paths=paths,
        )
    )
    run_service = RunService(
        sessions=sessions_repo,
        runs=runs_repo,
        artifacts=artifacts_repo,
        paths=paths,
        publisher=publisher,
        task_manager=task_manager,
    )

    session = await session_service.create_session()
    request = ResearchRequest(
        question=(
            "调研 DeepAgents 与 LangGraph 在 AI Agent 编排上的主要差异，"
            "重点比较：子智能体委派方式、状态持久化、工具调用机制。"
        ),
        max_web_sources=5,
        constraints=["只使用公开网页来源"],
        focus_areas=["子智能体委派方式", "状态持久化", "工具调用机制"],
    )
    run = await run_service.create_run(session.session_id, request)
    print(f"Created session={session.session_id} run={run.run_id} status={run.status}")

    # Poll run status until terminal. 真实 DeepSeek 多轮 + Tavily 检索可能耗时数分钟。
    for _ in range(300):
        await asyncio.sleep(2)
        current = await runs_repo.get(run.run_id)
        if current is None:
            print("Run disappeared")
            return 1
        if current.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            run = current
            break
    else:
        print("Timeout waiting for run completion")
        return 1

    print(f"\nFinal status: {run.status}")
    print(f"Error code: {run.error_code}")
    print(f"Error message: {run.error_message}")
    print(f"Report artifact id: {run.report_artifact_id}")

    if run.report_artifact_id:
        artifact = await artifacts_repo.get(run.report_artifact_id)
        if artifact:
            report_path = Path(artifact.original_storage_path)
            print(f"Report path: {report_path}")
            if report_path.exists():
                content = report_path.read_text(encoding="utf-8")
                print(f"\n--- Report markdown ({len(content)} chars) ---\n")
                print(content[:4000])
                if len(content) > 4000:
                    print(f"\n... ({len(content) - 4000} chars truncated)")
            else:
                print("Report file not found on disk")
        else:
            print("Artifact not found in DB")

    # Print evidence count.
    evidence = await evidence_repo.list_for_run(run.run_id)
    print(f"\nEvidence records: {len(evidence)}")
    for item in evidence[:10]:
        print(f"  {item.id}: {item.source_type}/{item.evidence_level} - {item.title[:60]}")
    if len(evidence) > 10:
        print(f"  ... and {len(evidence) - 10} more")

    await conn.close()
    return 0 if run.status == RunStatus.SUCCEEDED else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
