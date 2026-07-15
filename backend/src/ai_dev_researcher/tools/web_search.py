from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.core.errors import ConfigurationError, SearchProviderError
from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.sessions import utc_now
from ai_dev_researcher.services.evidence_store import EvidenceStore


def _canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.strip()
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def _publisher_key(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_private_host(host: str) -> bool:
    lowered = host.lower()
    if lowered in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return True
    if lowered.startswith("10.") or lowered.startswith("192.168."):
        return True
    if re.match(r"^172\.(1[6-9]|2\d|3[0-1])\.", lowered):
        return True
    return False


async def search_web_impl(
    *,
    context: RunContext,
    store: EvidenceStore,
    query: str,
    max_results: int = 5,
) -> dict:
    if not context.settings.tavily_api_key:
        raise ConfigurationError("TAVILY_API_KEY is required for web search")
    try:
        from tavily import AsyncTavilyClient
    except ImportError as exc:
        raise SearchProviderError("tavily client unavailable") from exc

    client = AsyncTavilyClient(api_key=context.settings.tavily_api_key)
    try:
        response = await client.search(query=query, max_results=max_results, include_raw_content=False)
    except Exception as exc:  # noqa: BLE001
        raise SearchProviderError(f"tavily search failed: {exc}") from exc

    items: list[dict] = []
    seen: set[str] = set()
    for rank, result in enumerate(response.get("results", []), start=1):
        url = _canonical_url(str(result.get("url", "")))
        if not url or url in seen:
            continue
        if _is_private_host(urlparse(url).netloc):
            continue
        seen.add(url)
        evidence_id = await store.allocate_web_id()
        record = EvidenceRecord(
            id=evidence_id,
            run_id=context.run_id,
            source_type="web",
            evidence_level="search_snippet",
            title=str(result.get("title") or url),
            locator=url,
            canonical_url=url,
            publisher_key=_publisher_key(url),
            excerpt=str(result.get("content") or result.get("snippet") or "")[:1000],
            query=query,
            result_rank=rank,
            retrieved_at=utc_now(),
        )
        await store.add(record)
        items.append(
            {
                "evidence_id": evidence_id,
                "title": record.title,
                "url": url,
                "snippet": store.excerpt(record, limit=240),
                "evidence_level": record.evidence_level,
            }
        )
    return {"items": items, "query": query}


async def extract_web_sources_impl(
    *,
    context: RunContext,
    store: EvidenceStore,
    evidence_ids: list[str],
) -> dict:
    if not context.settings.tavily_api_key:
        raise ConfigurationError("TAVILY_API_KEY is required for web extract")
    try:
        from tavily import AsyncTavilyClient
    except ImportError as exc:
        raise SearchProviderError("tavily client unavailable") from exc

    client = AsyncTavilyClient(api_key=context.settings.tavily_api_key)
    updated: list[dict] = []
    for evidence_id in evidence_ids:
        record = await store.get(evidence_id)
        if record is None or not record.canonical_url:
            continue
        try:
            response = await client.extract(urls=[record.canonical_url])
        except Exception:  # noqa: BLE001
            continue
        results = response.get("results") or []
        if not results:
            continue
        body = str(results[0].get("raw_content") or results[0].get("content") or "")[:4000]
        if not body:
            continue
        upgraded = record.model_copy(
            update={
                "evidence_level": "first_party",
                "excerpt": body,
            }
        )
        await store.add(upgraded)
        updated.append(
            {
                "evidence_id": evidence_id,
                "evidence_level": upgraded.evidence_level,
                "excerpt": store.excerpt(upgraded, limit=240),
            }
        )
    return {"updated": updated}
