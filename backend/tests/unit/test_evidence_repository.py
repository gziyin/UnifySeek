from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db


def _record(run_id, *, artifact_id: UUID | None) -> EvidenceRecord:
    return EvidenceRecord(
        id="D1",
        run_id=run_id,
        source_type="document",
        evidence_level="user_document",
        title="title",
        locator="lines 1-2",
        excerpt="excerpt",
        artifact_id=artifact_id,
    )


@pytest.fixture
async def repo(tmp_path: Path):
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    try:
        yield EvidenceRepository(conn), uuid4()
    finally:
        await conn.close()


async def test_create_update_and_list_round_trip_nullable_artifact_id(repo):
    evidence_repo, run_id = repo
    artifact_id = uuid4()
    record = _record(run_id, artifact_id=artifact_id)

    await evidence_repo.create(record)
    loaded = (await evidence_repo.list_for_run(run_id))[0]
    assert loaded.artifact_id == artifact_id

    await evidence_repo.update(record.model_copy(update={"artifact_id": None}))
    loaded = (await evidence_repo.list_for_run(run_id))[0]
    assert loaded.artifact_id is None


async def test_old_evidence_table_migrates_and_supports_create_update_list(tmp_path: Path):
    conn = await connect(str(tmp_path / "legacy.db"))
    try:
        await conn.executescript(
            """
            CREATE TABLE evidence (
                run_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                evidence_level TEXT NOT NULL,
                title TEXT NOT NULL,
                locator TEXT NOT NULL,
                canonical_url TEXT,
                publisher_key TEXT,
                excerpt TEXT NOT NULL,
                page INTEGER,
                line_start INTEGER,
                line_end INTEGER,
                query TEXT,
                result_rank INTEGER,
                retrieved_at TEXT NOT NULL,
                PRIMARY KEY (run_id, evidence_id)
            );
            """
        )
        await conn.commit()

        await init_db(conn)
        columns = await conn.execute_fetchall("PRAGMA table_info(evidence)")
        assert "artifact_id" in {row[1] for row in columns}

        evidence_repo = EvidenceRepository(conn)
        run_id = uuid4()
        artifact_id = uuid4()
        record = _record(run_id, artifact_id=artifact_id)
        await evidence_repo.create(record)
        assert (await evidence_repo.list_for_run(run_id))[0].artifact_id == artifact_id

        await evidence_repo.update(record.model_copy(update={"artifact_id": None}))
        assert (await evidence_repo.list_for_run(run_id))[0].artifact_id is None
    finally:
        await conn.close()
