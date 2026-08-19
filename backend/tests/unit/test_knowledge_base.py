from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.core.errors import KnowledgeBaseError
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.tools.knowledge_base import (
    KB_BUDGET_EXHAUSTED_GUIDANCE,
    KbToolBudget,
    list_knowledge_base_entries_impl,
    read_knowledge_base_file_impl,
    record_knowledge_base_evidence_impl,
)


def test_budget_exhausted_guidance_is_not_misleading():
    """#44 D2：预算耗尽引导语不得误导模型「知识库无关/已充分检索」，
    应如实说明是配额用尽并引导记录 unknowns。"""
    assert "KB 软预算已用尽" in KB_BUDGET_EXHAUSTED_GUIDANCE
    assert "无法继续检索/读取知识库" in KB_BUDGET_EXHAUSTED_GUIDANCE
    assert "unknowns" in KB_BUDGET_EXHAUSTED_GUIDANCE
    # 旧误导措辞必须移除（曾诱导模型放弃高度相关的知识库）。
    assert "已充分检索" not in KB_BUDGET_EXHAUSTED_GUIDANCE
    assert "或不相关" not in KB_BUDGET_EXHAUSTED_GUIDANCE


@pytest.fixture
async def env(tmp_path: Path):
    kb_root = tmp_path / "knowledge_base"
    kb_root.mkdir(parents=True)
    (kb_root / "src").mkdir(parents=True)
    (kb_root / "README.md").write_text(
        "# Knowledge Base\n\nLocal framework sources live here.\n",
        encoding="utf-8",
    )
    (kb_root / "src" / "main.py").write_text(
        "def main():\n    print('hello')\n",
        encoding="utf-8",
    )
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        knowledge_base_root=kb_root,
    )
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    paths = WorkspacePaths(settings.sessions_root, knowledge_base_root=kb_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)

    conn = None

    async def _build_store() -> EvidenceStore:
        nonlocal conn
        conn = await connect(str(settings.db_path))
        await init_db(conn)
        return EvidenceStore(
            run_id=run_id,
            session_id=session_id,
            evidence_repo=EvidenceRepository(conn),
            paths=paths,
        )

    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="q",
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=paths,
        settings=settings,
    )
    yield context, _build_store, kb_root
    if conn is not None:
        await conn.close()


async def test_list_entries(env):
    context, _build_store, _root = env
    result = await list_knowledge_base_entries_impl(context=context, path=".")
    names = {(item["name"], item["type"]) for item in result["entries"]}
    assert ("README.md", "file") in names
    assert ("src", "dir") in names
    assert result["root"] == str(_root.resolve())


async def test_list_entries_subdir(env):
    context, _build_store, _root = env
    result = await list_knowledge_base_entries_impl(context=context, path="src")
    assert result["entries"] == [{"name": "main.py", "type": "file", "path": "src/main.py"}]


async def test_read_file(env):
    context, _build_store, _root = env
    result = await read_knowledge_base_file_impl(context=context, path="README.md", offset=0, limit=4000)
    assert result["path"] == "README.md"
    assert "Knowledge Base" in result["text"]
    assert result["total_chars"] > 0


async def test_read_file_with_offset(env):
    context, _build_store, _root = env
    result = await read_knowledge_base_file_impl(context=context, path="README.md", offset=5, limit=10)
    assert result["text"] == "# Knowledge Base\n\n"[5:15]


async def test_path_escape_rejected(env):
    context, _build_store, _root = env
    for bad in ["../secret.txt", "..", "C:/Windows/system32", "/etc/passwd", "a/../../b"]:
        with pytest.raises(KnowledgeBaseError):
            await read_knowledge_base_file_impl(context=context, path=bad)


async def test_missing_file_rejected(env):
    context, _build_store, _root = env
    with pytest.raises(KnowledgeBaseError):
        await read_knowledge_base_file_impl(context=context, path="nope.txt")


async def test_unsupported_extension_rejected(env):
    context, _build_store, _root = env
    ( _root / "x.bin").write_bytes(b"binary")
    with pytest.raises(KnowledgeBaseError):
        await read_knowledge_base_file_impl(context=context, path="x.bin")


async def test_record_evidence_allocates_k_id(env):
    context, build_store, _root = env
    store = await build_store()
    result = await record_knowledge_base_evidence_impl(
        context=context,
        store=store,
        path="src/main.py",
        title="entry point",
        excerpt="def main(): ...",
        line_start=1,
        line_end=2,
    )
    assert result["evidence_id"].startswith("K")
    assert "kb:src/main.py lines 1-2" in result["locator"]
    ledger = await store.list_for_run()
    assert len(ledger) == 1
    assert ledger[0].source_type == "knowledge_base"
    assert ledger[0].evidence_level == "first_party"


async def test_record_evidence_missing_file_rejected(env):
    context, build_store, _root = env
    store = await build_store()
    with pytest.raises(KnowledgeBaseError):
        await record_knowledge_base_evidence_impl(
            context=context,
            store=store,
            path="missing.py",
            title="t",
            excerpt="e",
            line_start=1,
            line_end=2,
        )


# ---------------------------------------------------------------------------
# 批次A：record 必须绑定本 run 内搜索候选（候选注册 + 重叠行号 + 相关性阈值），
# 重复候选幂等，K 证据数量上限按模式（short/medium/long=3/5/8）。
# ---------------------------------------------------------------------------


async def _record_with_guard(context, store, path: str, start: int, end: int, guard):
    return await record_knowledge_base_evidence_impl(
        context=context,
        store=store,
        path=path,
        title=path,
        excerpt="excerpt",
        line_start=start,
        line_end=end,
        kb_guard=guard,
    )


async def test_record_without_candidate_is_rejected(env):
    """未搜索过该候选：record 被拒绝（note=candidate_rejected），不落账本。"""
    context, build_store, _root = env
    store = await build_store()
    guard = KbToolBudget(limit=10, k_evidence_limit=0)
    result = await _record_with_guard(context, store, "src/main.py", 1, 2, guard)
    assert result["note"] == "candidate_rejected"
    assert await store.list_for_run() == []


async def test_record_after_candidate_registered_succeeds(env):
    """先在 run 内搜索注册候选（路径+重叠行号+满足阈值），record 成功落账本。"""
    context, build_store, _root = env
    store = await build_store()
    guard = KbToolBudget(limit=10, k_evidence_limit=0)
    guard.register_candidate("src/main.py", 1, 3, score=0.9)
    result = await _record_with_guard(context, store, "src/main.py", 2, 3, guard)
    assert result["evidence_id"].startswith("K")
    assert "candidate_rejected" not in (result.get("note") or "")
    ledger = await store.list_for_run()
    assert len(ledger) == 1
    assert ledger[0].source_type == "knowledge_base"


async def test_record_candidate_range_must_overlap(env):
    """候选行号不重叠：record 被拒（同路径但非重叠区间视为不同候选）。"""
    context, build_store, _root = env
    store = await build_store()
    guard = KbToolBudget(limit=10, k_evidence_limit=0)
    guard.register_candidate("src/main.py", 1, 3, score=0.9)
    result = await _record_with_guard(context, store, "src/main.py", 100, 200, guard)
    assert result["note"] == "candidate_rejected"
    assert await store.list_for_run() == []


async def test_record_duplicate_candidate_is_idempotent(env):
    """同一候选重复 record：返回已记录的 evidence_id（note=duplicate），账本不重复。"""
    context, build_store, _root = env
    store = await build_store()
    guard = KbToolBudget(limit=10, k_evidence_limit=0)
    guard.register_candidate("src/main.py", 1, 3, score=0.9)
    first = await _record_with_guard(context, store, "src/main.py", 2, 3, guard)
    second = await _record_with_guard(context, store, "src/main.py", 1, 2, guard)
    assert first["evidence_id"] == second["evidence_id"]
    assert second["note"] == "duplicate"
    ledger = await store.list_for_run()
    assert len(ledger) == 1
    assert ledger[0].id == first["evidence_id"]


async def test_record_respects_k_evidence_limit(env):
    """K 证据数量上限：k_evidence_limit=1 时第二个不同候选的 record 被拒（note=budget_exceeded）。"""
    context, build_store, _root = env
    store = await build_store()
    guard = KbToolBudget(limit=10, k_evidence_limit=1)
    guard.register_candidate("src/main.py", 1, 3, score=0.9)
    guard.register_candidate("README.md", 1, 3, score=0.9)
    first = await _record_with_guard(context, store, "src/main.py", 2, 3, guard)
    assert first["evidence_id"].startswith("K")
    second = await _record_with_guard(context, store, "README.md", 1, 2, guard)
    assert second["note"] == "budget_exceeded"
    assert "K" in second.get("guidance", "")
    ledger = await store.list_for_run()
    assert len(ledger) == 1


async def test_record_exempt_from_search_budget_but_not_k_cap(env):
    """record 不消耗 KB search/read/list 预算（#44 保持）：search 预算耗尽仍可 record；
    但 K 上限（k_evidence_limit）独立生效。"""
    context, build_store, _root = env
    store = await build_store()
    guard = KbToolBudget(limit=1, k_evidence_limit=2)
    guard.register_candidate("src/main.py", 1, 3, score=0.9)
    guard.register_candidate("README.md", 1, 3, score=0.9)
    # 模拟 search/read/list 已用尽：spend 掉全部 search 预算。
    assert guard.acquire() is True
    assert guard.acquire() is False  # 预算耗尽
    first = await _record_with_guard(context, store, "src/main.py", 2, 3, guard)
    assert first["note"] != "budget_exceeded"  # record 豁免 search 预算
    second = await _record_with_guard(context, store, "README.md", 1, 2, guard)
    assert second["note"] != "budget_exceeded"
    third = await _record_with_guard(context, store, "src/main.py", 1, 2, guard)
    assert third["note"] == "duplicate"  # 已记录候选取回，不占新配额
    ledger = await store.list_for_run()
    assert len(ledger) == 2  # K cap=2：两个不同候选各记一条


async def test_candidate_registration_requires_threshold(env):
    """注册候选带阈值：低于阈值的搜索结果不作为候选（record 仍被拒）。"""
    context, build_store, _root = env
    store = await build_store()
    guard = KbToolBudget(limit=10, k_evidence_limit=0)
    guard.register_candidate("src/main.py", 1, 3, score=0.05)  # 低于默认阈值 0.3
    result = await _record_with_guard(context, store, "src/main.py", 2, 3, guard)
    assert result["note"] == "candidate_rejected"
