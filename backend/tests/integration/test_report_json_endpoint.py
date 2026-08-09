from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.domain.artifacts import Artifact, ArtifactKind
from ai_dev_researcher.main import create_app


@pytest.fixture
async def client_app(tmp_path: Path):
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        fake_agent_mode=True,
        cors_origins="http://127.0.0.1:5173",
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, app


async def _make_artifact(container, session_id, *, kind: ArtifactKind, normalized_path: str | None) -> Artifact:
    artifact = Artifact(
        artifact_id=uuid4(),
        session_id=session_id,
        kind=kind,
        display_name="artifact",
        mime_type="text/markdown" if kind == ArtifactKind.REPORT else "text/plain",
        original_storage_path=str(container.paths.sessions_root / f"{uuid4()}.bin"),
        normalized_storage_path=normalized_path,
    )
    await container.artifacts.create(artifact)
    return artifact


async def test_report_json_returns_structured_report(client_app):
    client, app = client_app
    container = app.state.container
    session = await container.sessions.create()

    sidecar = container.paths.sessions_root / f"{uuid4()}.json"
    payload = {"title": "t", "sections": [{"heading": "H", "claims": []}], "unknowns": ["u"]}
    sidecar.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    artifact = await _make_artifact(
        container, session.session_id, kind=ArtifactKind.REPORT, normalized_path=str(sidecar)
    )

    resp = await client.get(f"/api/artifacts/{artifact.artifact_id}/report-json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["artifact_id"] == str(artifact.artifact_id)
    assert body["degraded"] is False
    assert body["reason"] is None
    assert body["report"] == payload


async def test_report_json_degraded_when_no_sidecar(client_app):
    client, app = client_app
    container = app.state.container
    session = await container.sessions.create()

    artifact = await _make_artifact(
        container, session.session_id, kind=ArtifactKind.REPORT, normalized_path=None
    )

    resp = await client.get(f"/api/artifacts/{artifact.artifact_id}/report-json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["artifact_id"] == str(artifact.artifact_id)
    assert body["report"] is None
    assert body["degraded"] is True
    assert body["reason"] == "no structured report"


async def test_report_json_rejects_non_report_artifact(client_app):
    client, app = client_app
    container = app.state.container
    session = await container.sessions.create()

    upload_path = container.paths.sessions_root / f"{uuid4()}.txt"
    upload_path.write_text("some content", encoding="utf-8")
    artifact = await _make_artifact(
        container, session.session_id, kind=ArtifactKind.UPLOAD, normalized_path=str(upload_path)
    )

    resp = await client.get(f"/api/artifacts/{artifact.artifact_id}/report-json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["artifact_id"] == str(artifact.artifact_id)
    assert body["report"] is None
    assert body["degraded"] is True
    assert body["reason"] == "no structured report"


async def test_report_json_missing_artifact_returns_404(client_app):
    client, _app = client_app
    resp = await client.get(f"/api/artifacts/{uuid4()}/report-json")
    assert resp.status_code == 404
    assert resp.json()["code"] == "ARTIFACT_NOT_FOUND"


@pytest.mark.asyncio
async def test_report_json_after_full_fake_run(client_app):
    """Fake executor 必须落盘 JSON sidecar：完整 run 后 report-json 应返回结构化报告。"""
    client, _app = client_app

    session_resp = await client.post("/api/sessions")
    session_id = session_resp.json()["session_id"]

    run_resp = await client.post(
        f"/api/sessions/{session_id}/runs",
        json={"question": "fake executor report-json 端到端", "max_web_sources": 5},
    )
    assert run_resp.status_code == 202
    run_id = run_resp.json()["run_id"]

    report_artifact_id = None
    for _ in range(100):
        body = (await client.get(f"/api/runs/{run_id}")).json()
        if body["status"] == "succeeded":
            report_artifact_id = body["report_artifact_id"]
            break
        if body["status"] in {"failed", "cancelled", "interrupted"}:
            pytest.fail(f"unexpected terminal status: {body}")
        await asyncio.sleep(0.02)
    assert report_artifact_id is not None

    resp = await client.get(f"/api/artifacts/{report_artifact_id}/report-json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is False
    assert body["report"] is not None
    assert body["report"]["title"] == "DeepAgents 技术调研（Fake Slice）"
    assert body["report"]["sections"][0]["claims"]
