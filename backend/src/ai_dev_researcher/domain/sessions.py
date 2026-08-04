from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# Characters allowed inside a session slug: ASCII alphanumerics plus CJK
# ideographs. Everything else (spaces, punctuation, symbols) is collapsed
# into a single hyphen separator.
_SLUG_UNSAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")

DEFAULT_SLUG_MAX_LENGTH = 40


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_slug(question: str, max_length: int = DEFAULT_SLUG_MAX_LENGTH) -> str:
    """Convert a research question into a filesystem-safe slug.

    Keeps ASCII letters/digits and CJK ideographs; every run of other
    characters (spaces, punctuation, symbols) becomes a single hyphen.
    The result is stripped and truncated to ``max_length`` characters.
    An empty/unsafe input falls back to ``"session"``.
    """
    text = (question or "").strip()
    slug = _SLUG_UNSAFE.sub("-", text).strip("-")
    if not slug:
        return "session"
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or "session"


def session_dir_name(display_name: str, session_id: UUID) -> str:
    """Build the on-disk directory name for a session.

    Format: ``<slug>-<8位短uuid>``, e.g. ``deepagents-边界分析-3f8a2c1e``.
    The short uuid suffix is derived deterministically from the session id
    (first 8 hex chars), which keeps resolution stable across restarts.
    """
    slug = make_slug(display_name)
    short_uuid = session_id.hex[:8]
    return f"{slug}-{short_uuid}"


class Session(BaseModel):
    session_id: UUID = Field(default_factory=uuid4)
    display_name: str | None = None
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
