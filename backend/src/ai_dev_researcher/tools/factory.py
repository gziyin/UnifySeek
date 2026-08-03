from __future__ import annotations

from langchain_core.tools import StructuredTool

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.domain.reports import Disagreement, ResearchClaim, ReportSection
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.tools.document_reader import (
    list_run_documents_impl,
    read_run_document_impl,
    record_document_evidence_impl,
    search_run_documents_impl,
)
from ai_dev_researcher.tools.knowledge_base import (
    list_knowledge_base_entries_impl,
    read_knowledge_base_file_impl,
    record_knowledge_base_evidence_impl,
)
from ai_dev_researcher.tools.report_schema import SubmitResearchReportArgs
from ai_dev_researcher.tools.report_submitter import (
    get_evidence_ledger_impl,
    submit_research_report_impl,
)
from ai_dev_researcher.tools.web_search import extract_web_sources_impl, search_web_impl


def create_web_tools(context: RunContext, store: EvidenceStore) -> list[StructuredTool]:
    async def search_web(query: str, max_results: int = 5) -> dict:
        return await search_web_impl(
            context=context,
            store=store,
            query=query,
            max_results=max_results,
        )

    async def extract_web_sources(evidence_ids: list[str]) -> dict:
        return await extract_web_sources_impl(
            context=context,
            store=store,
            evidence_ids=evidence_ids,
        )

    return [
        StructuredTool.from_function(
            coroutine=search_web,
            name="search_web",
            description="Search the public web and create search_snippet evidence records.",
        ),
        StructuredTool.from_function(
            coroutine=extract_web_sources,
            name="extract_web_sources",
            description="Extract full text for previously discovered web evidence IDs.",
        ),
    ]


def create_document_tools(
    context: RunContext,
    store: EvidenceStore,
    artifacts: ArtifactRepository,
    vector_store=None,
) -> list[StructuredTool]:
    async def list_run_documents() -> dict:
        return await list_run_documents_impl(context=context, artifacts=artifacts)

    async def read_run_document(artifact_id: str, offset: int = 0, limit: int = 4000) -> dict:
        return await read_run_document_impl(
            context=context,
            artifacts=artifacts,
            artifact_id=artifact_id,
            offset=offset,
            limit=limit,
        )

    async def record_document_evidence(
        artifact_id: str,
        title: str,
        excerpt: str,
        line_start: int,
        line_end: int,
        page: int | None = None,
    ) -> dict:
        return await record_document_evidence_impl(
            context=context,
            store=store,
            artifacts=artifacts,
            artifact_id=artifact_id,
            title=title,
            excerpt=excerpt,
            line_start=line_start,
            line_end=line_end,
            page=page,
        )

    async def list_knowledge_base_entries(path: str = ".") -> dict:
        return await list_knowledge_base_entries_impl(context=context, path=path)

    async def read_knowledge_base_file(path: str, offset: int = 0, limit: int = 4000) -> dict:
        return await read_knowledge_base_file_impl(
            context=context,
            path=path,
            offset=offset,
            limit=limit,
        )

    async def record_knowledge_base_evidence(
        path: str,
        title: str,
        excerpt: str,
        line_start: int,
        line_end: int,
    ) -> dict:
        return await record_knowledge_base_evidence_impl(
            context=context,
            store=store,
            path=path,
            title=title,
            excerpt=excerpt,
            line_start=line_start,
            line_end=line_end,
        )

    async def search_run_documents(
        query: str,
        artifact_ids: list[str] | None = None,
        top_k: int = 5,
    ) -> dict:
        return await search_run_documents_impl(
            context=context,
            artifacts=artifacts,
            vector_store=vector_store,
            query=query,
            artifact_ids=artifact_ids,
            top_k=top_k,
        )

    return [
        StructuredTool.from_function(
            coroutine=list_run_documents,
            name="list_run_documents",
            description="List normalized documents authorized for this run.",
        ),
        StructuredTool.from_function(
            coroutine=search_run_documents,
            name="search_run_documents",
            description=(
                "Semantically search indexed run documents (RAG) and return matching "
                "chunks with line ranges. Use this first to locate relevant snippets, "
                "then read_run_document for exact context."
            ),
        ),
        StructuredTool.from_function(
            coroutine=read_run_document,
            name="read_run_document",
            description="Read a chunk of normalized document text by artifact ID.",
        ),
        StructuredTool.from_function(
            coroutine=record_document_evidence,
            name="record_document_evidence",
            description="Record document evidence with line/page locator.",
        ),
        StructuredTool.from_function(
            coroutine=list_knowledge_base_entries,
            name="list_knowledge_base_entries",
            description=(
                "List files/dirs in the local knowledge base (project workspace root). "
                "Path is relative to the knowledge base root; use '.' for the root itself."
            ),
        ),
        StructuredTool.from_function(
            coroutine=read_knowledge_base_file,
            name="read_knowledge_base_file",
            description=(
                "Read a chunk of a local knowledge base file (relative path, e.g. "
                "'deepagents-0.6.2/README.md'). Supported: md/txt/py/json/yaml/toml."
            ),
        ),
        StructuredTool.from_function(
            coroutine=record_knowledge_base_evidence,
            name="record_knowledge_base_evidence",
            description="Record evidence from the local knowledge base with line range.",
        ),
    ]


def create_orchestrator_tools(
    context: RunContext,
    store: EvidenceStore,
    artifacts: ArtifactRepository,
) -> list[StructuredTool]:
    async def search_web(query: str, max_results: int = 5) -> dict:
        return await search_web_impl(
            context=context,
            store=store,
            query=query,
            max_results=max_results,
        )

    async def extract_web_sources(evidence_ids: list[str]) -> dict:
        return await extract_web_sources_impl(
            context=context,
            store=store,
            evidence_ids=evidence_ids,
        )

    async def get_evidence_ledger() -> dict:
        return await get_evidence_ledger_impl(store=store)

    async def submit_research_report(
        title: str,
        executive_summary_claim_ids: list[str],
        sections: list[ReportSection],
        recommendations: list[ResearchClaim],
        disagreements: list[Disagreement] | None = None,
        unknowns: list[str] | None = None,
    ) -> dict:
        report_data = {
            "title": title,
            "executive_summary_claim_ids": executive_summary_claim_ids,
            "sections": [s.model_dump(mode="json") for s in sections],
            "recommendations": [c.model_dump(mode="json") for c in recommendations],
            "disagreements": [d.model_dump(mode="json") for d in (disagreements or [])],
            "unknowns": unknowns or [],
        }
        return await submit_research_report_impl(
            store=store,
            artifacts=artifacts,
            sessions_root=context.paths.sessions_root,
            session_id=context.session_id,
            run_id=context.run_id,
            report_data=report_data,
        )

    return [
        StructuredTool.from_function(
            coroutine=search_web,
            name="search_web",
            description="Search the public web and create search_snippet evidence records.",
        ),
        StructuredTool.from_function(
            coroutine=extract_web_sources,
            name="extract_web_sources",
            description="Extract full text for previously discovered web evidence IDs.",
        ),
        StructuredTool.from_function(
            coroutine=get_evidence_ledger,
            name="get_evidence_ledger",
            description="Read the current run evidence ledger.",
        ),
        StructuredTool.from_function(
            coroutine=submit_research_report,
            name="submit_research_report",
            description=(
                "提交最终结构化研究报告。硬性前置条件：1) 必须先调用 search_web（必要时 "
                "extract_web_sources）收集网页证据；2) 先调用 get_evidence_ledger 核对证据 ID；"
                "3) 报告内每个 claim 的 citation_ids 必须引用 ledger 中真实存在的证据 ID"
                "（形如 S1/S2/D1）。返回 artifact_id。"
            ),
            args_schema=SubmitResearchReportArgs,
        ),
    ]
