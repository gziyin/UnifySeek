from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import aiosqlite

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    cancel_requested_at TEXT,
    error_code TEXT,
    error_message TEXT,
    report_artifact_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_session_status ON runs(session_id, status);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    run_id TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('upload', 'report')),
    display_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    parse_status TEXT NOT NULL,
    original_storage_path TEXT,
    normalized_storage_path TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id);

CREATE TABLE IF NOT EXISTS evidence (
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

-- 并发安全的证据 ID 预留（A1：Evidence ID 原子分配）。
-- 每 (run_id, source_type) 一行，source_type 为单字母前缀（S=web/D=document/K=knowledge_base），
-- next_value 表示「已预留的最大数字后缀」；allocate_ids 以单条 INSERT..ON CONFLICT..RETURNING
-- 原子自增预留，允许 ID 空洞、杜绝并发重复。
CREATE TABLE IF NOT EXISTS evidence_sequences (
    run_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    next_value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, source_type)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(run_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_events_run_seq ON events(run_id, seq);
"""


async def connect(db_path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn



T = TypeVar("T")


async def run_atomic(conn: aiosqlite.Connection, fn: Callable[[], T]) -> T:
    """Run a sqlite3 worker function as one non-interleavable aiosqlite unit.

    This intentionally uses aiosqlite's private _execute API. uv.lock currently
    pins aiosqlite 0.22.1; rerun evidence repository concurrency tests before
    upgrading aiosqlite.
    """
    return await conn._execute(fn)


async def init_db(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_SQL)
    await conn.commit()
