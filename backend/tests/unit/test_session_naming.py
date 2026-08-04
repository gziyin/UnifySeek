from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.domain.runs import ResearchRequest
from ai_dev_researcher.domain.sessions import (
    Session,
    make_slug,
    session_dir_name,
)
from ai_dev_researcher.main import create_app
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.run_service import RunService
from ai_dev_researcher.services.session_service import SessionService
from ai_dev_researcher.services.task_manager import TaskManager
from ai_dev_researcher.storage.paths import WorkspacePaths


# ---------------------------------------------------------------------------
# slug 生成
# ---------------------------------------------------------------------------


def test_make_slug_chinese_keeps_ideographs():
    assert make_slug("deepagents 边界分析") == "deepagents-边界分析"


def test_make_slug_english_collapses_spaces_and_punctuation():
    assert make_slug("What is DeepAgents?") == "What-is-DeepAgents"


def test_make_slug_special_characters_only_falls_back():
    assert make_slug("!!! ??? ***") == "session"


def test_make_slug_truncates_to_40_chars():
    long_question = "请详细分析深度智能体框架在个人项目中的适用边界与成本收益" * 3
    slug = make_slug(long_question)
    assert len(slug) <= 40
    assert "-" not in slug.strip("-")


def test_make_slug_strips_leading_trailing_separators():
    assert make_slug("  !! 边界分析  ") == "边界分析"


# ---------------------------------------------------------------------------
# 目录命名 slug-8位短uuid
# ---------------------------------------------------------------------------


def test_session_dir_name_format():
    session_id = uuid4()
    name = session_dir_name("deepagents 边界分析", session_id)
    short = session_id.hex[:8]
    assert name == f"deepagents-边界分析-{short}"
    assert name.endswith(f"-{short}")


def test_session_dir_name_is_deterministic():
    session_id = uuid4()
    assert session_dir_name("deepagents 边界分析", session_id) == session_dir_name(
        "deepagents 边界分析", session_id
    )


# ---------------------------------------------------------------------------
# WorkspacePaths.session_dir 按 display_name 解析
# ---------------------------------------------------------------------------


def test_session_dir_with_display_name_uses_slug(tmp_path: Path):
    paths = WorkspacePaths(tmp_path)
    session_id = uuid4()
    resolved = paths.session_dir(session_id, display_name="deepagents 边界分析")
    expected = tmp_path / f"deepagents-边界分析-{session_id.hex[:8]}"
    assert resolved == expected.resolve()


def test_session_dir_legacy_uuid_dir_kept(tmp_path: Path):
    """存量纯 UUID 目录必须继续指向原目录。"""
    paths = WorkspacePaths(tmp_path)
    session_id = uuid4()
    legacy = tmp_path / str(session_id)
    legacy.mkdir(parents=True)
    (legacy / "uploads").mkdir()

    resolved = paths.session_dir(session_id)
    assert resolved == legacy.resolve()
    # 即使带 display_name，只要存量 UUID 目录已存在，也不改路径（不破坏存量）。
    resolved_with_name = paths.session_dir(session_id, display_name="deepagents 边界分析")
    assert resolved_with_name == legacy.resolve()


def test_session_dir_reuses_existing_slug_dir_when_only_session_id(tmp_path: Path):
    """首次 run 已创建 slug 目录后，仅凭 session_id 的调用方仍解析到同一目录。"""
    paths = WorkspacePaths(tmp_path)
    session_id = uuid4()
    slug_dir = tmp_path / f"deepagents-边界分析-{session_id.hex[:8]}"
    slug_dir.mkdir(parents=True)

    assert paths.session_dir(session_id) == slug_dir.resolve()


def test_ensure_run_layout_uses_slug_dir(tmp_path: Path):
    paths = WorkspacePaths(tmp_path)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id, display_name="deepagents 边界分析")

    session_dir = tmp_path / f"deepagents-边界分析-{session_id.hex[:8]}"
    assert (session_dir / "runs" / str(run_id) / "evidence").is_dir()
    assert (session_dir / "runs" / str(run_id) / "reports").is_dir()
    assert paths.evidence_dir(session_id, run_id) == (
        session_dir / "runs" / str(run_id) / "evidence"
    ).resolve()


def test_legacy_ensure_run_layout_still_uses_uuid_dir(tmp_path: Path):
    """存量 UUID 布局的 run/evidence/报告路径必须保持不变。"""
    paths = WorkspacePaths(tmp_path)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)

    session_dir = tmp_path / str(session_id)
    assert paths.run_dir(session_id, run_id) == (session_dir / "runs" / str(run_id)).resolve()
    assert paths.evidence_dir(session_id, run_id) == (
        session_dir / "runs" / str(run_id) / "evidence"
    ).resolve()
    assert paths.reports_dir(session_id, run_id) == (
        session_dir / "runs" / str(run_id) / "reports"
    ).resolve()


# ---------------------------------------------------------------------------
# SessionRepository：display_name 持久化 + 存量表迁移
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repository_persists_display_name(tmp_path: Path):
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    repo = SessionRepository(conn)
    session = Session(display_name="deepagents-边界分析")
    created = await repo.create(session)
    assert created.display_name == "deepagents-边界分析"

    fetched = await repo.get(created.session_id)
    assert fetched is not None
    assert fetched.display_name == "deepagents-边界分析"
    await conn.close()


@pytest.mark.asyncio
async def test_repository_legacy_row_display_name_none(tmp_path: Path):
    """存量 Session（无 display_name 列/值为 NULL）回退为 None。"""
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    # 直接按旧 schema 插入一行（display_name 列不存在）
    await conn.execute(
        "INSERT INTO sessions (session_id, status, created_at, updated_at) VALUES (?, 'active', ?, ?)",
        (str(uuid4()), "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
    )
    await conn.commit()

    repo = SessionRepository(conn)
    items = await repo.list()
    assert len(items) == 1
    assert items[0].display_name is None
    await conn.close()


@pytest.mark.asyncio
async def test_repository_update_display_name(tmp_path: Path):
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    repo = SessionRepository(conn)
    created = await repo.create(Session())

    updated = await repo.update_display_name(created.session_id, "deepagents-边界分析")
    assert updated is not None
    assert updated.display_name == "deepagents-边界分析"
    await conn.close()


@pytest.mark.asyncio
async def test_repository_list_sessions(tmp_path: Path):
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    repo = SessionRepository(conn)
    await repo.create(Session(display_name="first"))
    await repo.create(Session(display_name="second"))

    items = await repo.list()
    assert len(items) == 2
    assert {item.display_name for item in items} == {"first", "second"}
    await conn.close()


# ---------------------------------------------------------------------------
# SessionService：创建 / 列表 / set_display_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_create_and_list(tmp_path: Path):
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    paths = WorkspacePaths(tmp_path / "sessions")
    service = SessionService(SessionRepository(conn), paths)

    session = await service.create_session()
    assert session.display_name is None
    assert len(await service.list_sessions()) == 1
    await conn.close()


@pytest.mark.asyncio
async def test_service_set_display_name_creates_slug_layout(tmp_path: Path):
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    paths = WorkspacePaths(tmp_path / "sessions")
    service = SessionService(SessionRepository(conn), paths)

    session = await service.create_session()
    updated = await service.set_display_name(session.session_id, "deepagents 边界分析")
    assert updated.display_name == "deepagents-边界分析"

    slug_dir = tmp_path / "sessions" / f"deepagents-边界分析-{session.session_id.hex[:8]}"
    assert slug_dir.is_dir()
    assert paths.session_dir(session.session_id) == slug_dir.resolve()
    await conn.close()


# ---------------------------------------------------------------------------
# API：session 创建 / 列表 适配（display_name 字段）
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(tmp_path: Path):
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        fake_agent_mode=True,
        cors_origins="http://127.0.0.1:5173",
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_api_create_session_returns_display_name(client: AsyncClient):
    resp = await client.post("/api/sessions")
    assert resp.status_code == 201
    body = resp.json()
    assert "display_name" in body
    assert body["display_name"] is None


@pytest.mark.asyncio
async def test_api_list_sessions(client: AsyncClient):
    first = await client.post("/api/sessions")
    second = await client.post("/api/sessions")
    assert first.status_code == 201
    assert second.status_code == 201

    resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert all("display_name" in item for item in items)


@pytest.mark.asyncio
async def test_api_get_session(client: AsyncClient):
    created = await client.post("/api/sessions")
    session_id = created.json()["session_id"]

    resp = await client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == session_id
    assert resp.json()["display_name"] is None


# ---------------------------------------------------------------------------
# first-run 命名：run_service.create_run 接线
# ---------------------------------------------------------------------------


async def _build_run_service(tmp_path: Path) -> tuple[RunService, WorkspacePaths, aiosqlite.Connection]:
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    paths = WorkspacePaths(tmp_path / "sessions")
    publisher = EventPublisher(EventRepository(conn))
    task_manager = TaskManager(_noop_executor_factory)
    service = RunService(
        sessions=SessionRepository(conn),
        runs=RunRepository(conn),
        artifacts=ArtifactRepository(conn),
        paths=paths,
        publisher=publisher,
        task_manager=task_manager,
    )
    return service, paths, conn


async def _noop_executor(run_id: UUID) -> None:
    return None


def _noop_executor_factory():
    return _noop_executor


async def _create_question() -> ResearchRequest:
    return ResearchRequest(question="deepagents 边界分析与个人项目适用性")


@pytest.mark.asyncio
async def test_first_run_names_session_dir_with_slug(tmp_path: Path):
    """新 session 无 display_name 时，首次 create_run 目录为 slug-8位短uuid。"""
    service, paths, conn = await _build_run_service(tmp_path)
    session = await service._sessions.create(Session())

    run = await service.create_run(session.session_id, await _create_question())

    updated = await service._sessions.get(session.session_id)
    assert updated is not None
    assert updated.display_name == "deepagents-边界分析与个人项目适用性"

    slug_dir = tmp_path / "sessions" / f"deepagents-边界分析与个人项目适用性-{session.session_id.hex[:8]}"
    assert (slug_dir / "runs" / str(run.run_id) / "evidence").is_dir()
    assert paths.session_dir(session.session_id) == slug_dir.resolve()
    await conn.close()


@pytest.mark.asyncio
async def test_existing_display_name_not_renamed(tmp_path: Path):
    """已有 display_name 的 session，再次 run 不重命名目录（粘性）。"""
    service, paths, conn = await _build_run_service(tmp_path)
    session = await service._sessions.create(Session(display_name="already-named"))

    run = await service.create_run(session.session_id, await _create_question())

    updated = await service._sessions.get(session.session_id)
    assert updated is not None
    assert updated.display_name == "already-named"

    slug_dir = tmp_path / "sessions" / f"already-named-{session.session_id.hex[:8]}"
    assert (slug_dir / "runs" / str(run.run_id) / "evidence").is_dir()
    assert paths.session_dir(session.session_id) == slug_dir.resolve()
    await conn.close()


@pytest.mark.asyncio
async def test_first_run_naming_failure_falls_back_to_legacy(tmp_path: Path, monkeypatch):
    """update_display_name 失败不阻塞 run 创建，回退 legacy UUID 目录。"""
    service, paths, conn = await _build_run_service(tmp_path)
    session = await service._sessions.create(Session())

    async def _boom(session_id, display_name):
        raise RuntimeError("naming failed")

    monkeypatch.setattr(service._sessions, "update_display_name", _boom)

    run = await service.create_run(session.session_id, await _create_question())

    updated = await service._sessions.get(session.session_id)
    assert updated is not None
    assert updated.display_name is None

    legacy_dir = tmp_path / "sessions" / str(session.session_id)
    assert (legacy_dir / "runs" / str(run.run_id) / "evidence").is_dir()
    await conn.close()
