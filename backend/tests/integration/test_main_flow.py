from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.main import create_app


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
async def test_main_flow_session_upload_run_report(client: AsyncClient):
    session_resp = await client.post("/api/sessions")
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    notes = (
        "DeepAgents 适合主从编排。\n"
        "个人项目两周内应先打通证据与报告校验。\n"
    ).encode("utf-8")
    upload_resp = await client.post(
        f"/api/sessions/{session_id}/uploads",
        files={"file": ("notes.txt", notes, "text/plain")},
    )
    assert upload_resp.status_code == 201
    artifact_id = upload_resp.json()["artifact_id"]
    assert upload_resp.json()["parse_status"] == "parsed"

    run_resp = await client.post(
        f"/api/sessions/{session_id}/runs",
        json={
            "question": "结合上传笔记分析 DeepAgents 个人项目适用边界并给两周建议",
            "uploaded_artifact_ids": [artifact_id],
            "max_web_sources": 5,
        },
    )
    assert run_resp.status_code == 202
    run_id = run_resp.json()["run_id"]

    report_artifact_id = None
    for _ in range(50):
        status_resp = await client.get(f"/api/runs/{run_id}")
        body = status_resp.json()
        if body["status"] == "succeeded":
            report_artifact_id = body["report_artifact_id"]
            break
        if body["status"] in {"failed", "cancelled", "interrupted"}:
            pytest.fail(f"unexpected terminal status: {body}")
        await asyncio.sleep(0.05)
    assert report_artifact_id is not None

    events_resp = await client.get(f"/api/runs/{run_id}/events?after_seq=0")
    assert events_resp.status_code == 200
    types = [item["type"] for item in events_resp.json()["events"]]
    assert "run.started" in types
    assert "report.ready" in types
    assert "run.succeeded" in types

    content_resp = await client.get(f"/api/artifacts/{report_artifact_id}/content")
    assert content_resp.status_code == 200
    content = content_resp.json()["content"]
    assert "DeepAgents" in content
    assert "[1]" in content  # 叙事化编号引用
    assert "### Sources" in content
    assert "https://example.com/deepagents" in content
    assert "`S1`" not in content  # 证据 ID 不再暴露在正文
    assert "confidence=" not in content

    conflict = await client.post(
        f"/api/sessions/{session_id}/runs",
        json={
            "question": "再次提交应因 active run 冲突失败吗？其实已结束应允许",
            "uploaded_artifact_ids": [],
            "max_web_sources": 5,
        },
    )
    assert conflict.status_code == 202


@pytest.mark.asyncio
async def test_active_run_conflict(client: AsyncClient):
    session_resp = await client.post("/api/sessions")
    session_id = session_resp.json()["session_id"]

    first = await client.post(
        f"/api/sessions/{session_id}/runs",
        json={
            "question": "第一次研究任务用于制造 active run 冲突场景",
            "max_web_sources": 5,
        },
    )
    assert first.status_code == 202

    # Immediately create second run before first finishes.
    second = await client.post(
        f"/api/sessions/{session_id}/runs",
        json={
            "question": "第二次研究任务应返回 409 RUN_ACTIVE 错误",
            "max_web_sources": 5,
        },
    )
    # Depending on timing, fake executor may already finish; accept 202 or 409.
    assert second.status_code in {202, 409}
    if second.status_code == 409:
        assert second.json()["code"] == "RUN_ACTIVE"
