from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import ValidationError

from ai_dev_researcher.core.errors import ReportValidationError
from ai_dev_researcher.domain.artifacts import Artifact, ArtifactKind, ParseStatus
from ai_dev_researcher.domain.reports import ResearchReport
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.artifacts import (
    atomic_write_text,
    collect_claims,
    render_report_markdown,
)


_DEGRADED_TITLE = "[DEGRADED] 研究报告生成失败"


async def get_evidence_ledger_impl(*, store: EvidenceStore) -> dict:
    records = await store.list_for_run()
    return {
        "items": [
            {
                "id": item.id,
                "source_type": item.source_type,
                "evidence_level": item.evidence_level,
                "title": item.title,
                "locator": item.locator,
                "excerpt": store.excerpt(item, limit=240),
            }
            for item in records
        ]
    }


def _render_degraded_markdown(report_data: dict, reason: str) -> str:
    lines = [
        f"# {_DEGRADED_TITLE}",
        "",
        "本报告未通过证据校验，已自动生成降级版本以便定位问题。",
        "",
        f"**失败原因**：{reason}",
        "",
        "## 模型原始提交数据",
        "",
        "```json",
    ]
    try:
        import json

        lines.append(json.dumps(report_data, ensure_ascii=False, indent=2, default=str))
    except Exception:
        lines.append(str(report_data))
    lines.extend(["```", ""])
    return "\n".join(lines)


async def _save_report_artifact(
    *,
    artifacts: ArtifactRepository,
    sessions_root,
    session_id: UUID,
    run_id: UUID,
    markdown: str,
    title: str,
) -> UUID:
    artifact_id = uuid4()
    report_path = (
        sessions_root / str(session_id) / "runs" / str(run_id) / "reports" / f"{artifact_id}.md"
    )
    atomic_write_text(report_path, markdown, root=sessions_root)
    artifact = Artifact(
        artifact_id=artifact_id,
        session_id=session_id,
        run_id=run_id,
        kind=ArtifactKind.REPORT,
        display_name="research-report.md",
        mime_type="text/markdown",
        size_bytes=len(markdown.encode("utf-8")),
        parse_status=ParseStatus.SKIPPED,
        original_storage_path=str(report_path),
    )
    await artifacts.create(artifact)
    return artifact_id


async def submit_research_report_impl(
    *,
    store: EvidenceStore,
    artifacts: ArtifactRepository,
    sessions_root,
    session_id: UUID,
    run_id: UUID,
    report_data: dict,
) -> dict:
    evidence_ids = {item.id for item in await store.list_for_run()}
    degrade_reason: str | None = None

    try:
        report = ResearchReport.model_validate(report_data)
        claims = collect_claims(report)

        for claim_id in report.executive_summary_claim_ids:
            if claim_id not in claims:
                raise ReportValidationError(f"unknown summary claim: {claim_id}")

        for claim in claims.values():
            if not claim.citation_ids:
                raise ReportValidationError(f"claim {claim.id} missing citations")
            unknown = [cid for cid in claim.citation_ids if cid not in evidence_ids]
            if unknown:
                raise ReportValidationError(
                    f"claim {claim.id} references unknown evidence: {unknown}"
                )
            if claim.confidence == "high":
                levels = []
                for cid in claim.citation_ids:
                    record = await store.get(cid)
                    if record:
                        levels.append(record.evidence_level)
                if all(level == "search_snippet" for level in levels):
                    raise ReportValidationError(
                        f"claim {claim.id} cannot be high confidence with only search snippets"
                    )
    except (ValidationError, ReportValidationError) as exc:
        degrade_reason = f"{type(exc).__name__}: {exc}"
        report = None

    if report is not None:
        markdown = render_report_markdown(report, collect_claims(report))
        title = report.title
        degraded = False
    else:
        markdown = _render_degraded_markdown(report_data, degrade_reason or "unknown")
        title = _DEGRADED_TITLE
        degraded = True

    artifact_id = await _save_report_artifact(
        artifacts=artifacts,
        sessions_root=sessions_root,
        session_id=session_id,
        run_id=run_id,
        markdown=markdown,
        title=title,
    )
    return {
        "artifact_id": str(artifact_id),
        "title": title,
        "degraded": degraded,
        "reason": degrade_reason,
    }
