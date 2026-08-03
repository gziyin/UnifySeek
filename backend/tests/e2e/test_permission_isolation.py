from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.tools.factory import (
    create_document_tools,
    create_orchestrator_tools,
    create_web_tools,
)


def _make_context(tmp_path: Path) -> RunContext:
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        knowledge_base_root=tmp_path / "kb",
        fake_agent_mode=False,
    )
    paths = WorkspacePaths(settings.sessions_root, knowledge_base_root=settings.knowledge_base_root)
    return RunContext(
        run_id=uuid4(),
        session_id=uuid4(),
        question="test",
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=paths,
        settings=settings,
    )


class _FakeStore:
    async def allocate_web_id(self) -> str:
        return "S1"

    async def allocate_document_id(self) -> str:
        return "D1"

    async def allocate_knowledge_base_id(self) -> str:
        return "K1"

    async def add(self, record) -> None:
        return None

    async def list_for_run(self):
        return []


def test_web_tools_have_no_filesystem_tools(tmp_path: Path):
    context = _make_context(tmp_path)
    tools = create_web_tools(context, _FakeStore())
    names = {tool.name for tool in tools}
    assert names == {"search_web", "extract_web_sources"}
    assert not names & {"read_file", "write_file", "edit_file", "execute", "ls", "glob", "grep"}


def test_document_tools_are_read_only(tmp_path: Path):
    context = _make_context(tmp_path)
    tools = create_document_tools(context, _FakeStore(), MagicMock())
    names = {tool.name for tool in tools}
    assert {
        "list_run_documents",
        "read_run_document",
        "record_document_evidence",
        "list_knowledge_base_entries",
        "read_knowledge_base_file",
        "record_knowledge_base_evidence",
    } <= names
    write_like = {"write_file", "edit_file", "delete", "execute", "upload"}
    assert not names & write_like


def test_orchestrator_tools_expose_no_filesystem(tmp_path: Path):
    context = _make_context(tmp_path)
    tools = create_orchestrator_tools(context, _FakeStore(), MagicMock())
    names = {tool.name for tool in tools}
    assert "search_web" in names
    assert "submit_research_report" in names
    assert not names & {"write_file", "edit_file", "delete", "execute", "ls", "glob", "grep"}


def test_knowledge_base_tools_owned_by_document_analyst_only(tmp_path: Path):
    """KB 工具只下放给 document-analyst，orchestrator 不直接暴露。"""
    context = _make_context(tmp_path)
    store = _FakeStore()
    artifacts = MagicMock()

    doc_tools = create_document_tools(context, store, artifacts)
    orch_tools = create_orchestrator_tools(context, store, artifacts)

    doc_names = {t.name for t in doc_tools}
    orch_names = {t.name for t in orch_tools}
    assert "read_knowledge_base_file" in doc_names
    assert "read_knowledge_base_file" not in orch_names
    assert "list_knowledge_base_entries" in doc_names
    assert "list_knowledge_base_entries" not in orch_names
