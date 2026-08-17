from __future__ import annotations

import json
from uuid import UUID, uuid4

from pydantic import ValidationError

from ai_dev_researcher.core.errors import ReportValidationError
from ai_dev_researcher.domain.artifacts import Artifact, ArtifactKind, ParseStatus
from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.reports import ReportSection, ResearchClaim, ResearchReport
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.artifacts import (
    atomic_write_text,
    collect_claims,
    render_report_markdown,
)
from ai_dev_researcher.storage.paths import WorkspacePaths


_DEGRADED_TITLE = "[DEGRADED] \u7814\u7a76\u62a5\u544a\u751f\u6210\u5931\u8d25"

# \u8bc1\u636e\u7b49\u7ea7\u5f3a\u5ea6\u6392\u5e8f\uff1ahigh confidence claim \u5fc5\u987b\u5f15\u7528\u81f3\u5c11\u4e00\u4e2a\u8fbe\u5230\u6b64\u5f3a\u5ea6\u53ca\u4ee5\u4e0a\u7684\u8bc1\u636e\u3002
_STRONG_EVIDENCE_LEVELS = {"first_party", "official_primary", "user_document"}


def _evidence_levels(
    evidence_by_id: dict[str, EvidenceRecord], citation_ids: list[str]
) -> list[str]:
    return [
        evidence_by_id[cid].evidence_level
        for cid in citation_ids
        if cid in evidence_by_id
    ]


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
        "\u672c\u62a5\u544a\u672a\u901a\u8fc7\u8bc1\u636e\u6821\u9a8c\uff0c\u5df2\u81ea\u52a8\u751f\u6210\u964d\u7ea7\u7248\u672c\u4ee5\u4fbf\u5b9a\u4f4d\u95ee\u9898\u3002",
        "",
        f"**\u5931\u8d25\u539f\u56e0**\uff1a{reason}",
        "",
        "## \u6a21\u578b\u539f\u59cb\u63d0\u4ea4\u6570\u636e",
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


def _heal_high_confidence(
    report: ResearchReport, evidence_by_id: dict[str, EvidenceRecord]
) -> tuple[ResearchReport, list[str]]:
    """\u53ef\u964d\u7ea7\uff08Issue 6a\uff09\uff1a\u5f53 high confidence \u4f46\u5f15\u7528\u8bc1\u636e\u5747\u4e3a\u5f31\u8bc1\u636e\u65f6\uff0c\u81ea\u52a8\u5c06\u8be5 claim \u964d\u7ea7\u4e3a medium\uff0c
    \u800c\u4e0d\u662f\u5c06\u6574\u4efd\u62a5\u544a\u964d\u7ea7\u4e3a DEGRADED\u3002\u8fd4\u56de (\u7528 claim \u4e0e\u4e0b\u964d\u7684 claim id \u5217\u8868)\u3002
    """
    changed_ids: list[str] = []

    def _heal_claim(claim: ResearchClaim) -> ResearchClaim:
        if claim.confidence != "high":
            return claim
        levels = _evidence_levels(evidence_by_id, claim.citation_ids)
        if any(level in _STRONG_EVIDENCE_LEVELS for level in levels):
            return claim
        changed_ids.append(claim.id)
        return claim.model_copy(update={"confidence": "medium"})

    def _heal_section(sec) -> "ReportSection":
        return sec.model_copy(
            update={
                "claims": [_heal_claim(c) for c in sec.claims],
                "subsections": [_heal_section(sub) for sub in sec.subsections],
            }
        )

    sections = [_heal_section(s) for s in report.sections]
    recommendations = [_heal_claim(c) for c in report.recommendations]
    if not changed_ids:
        return report, []
    return report.model_copy(update={"sections": sections, "recommendations": recommendations}), changed_ids


async def _save_report_artifact(
    *,
    artifacts: ArtifactRepository,
    paths: WorkspacePaths,
    session_id: UUID,
    run_id: UUID,
    markdown: str,
    title: str,
    display_name: str | None = None,
    report_json: dict | None = None,
) -> UUID:
    artifact_id = uuid4()
    # Issue 9: \u8d70 WorkspacePaths \u7c98\u6027\u89e3\u6790\uff08slug \u4f1a\u8bdd\u76ee\u5f55\uff09\uff0c\u4e25\u7981\u786c\u7f16\u7801 str(session_id)\u3002
    report_path = paths.report_path(session_id, run_id, artifact_id, display_name)
    atomic_write_text(report_path, markdown, root=paths.sessions_root)
    # Structured JSON sidecar: persisted alongside the markdown for the
    # interactive report viewer. Report artifacts keep the markdown in
    # ``original_storage_path`` (get_artifact_content unaffected) and the JSON
    # absolute path in ``normalized_storage_path``.
    report_json_path: str | None = None
    if report_json is not None:
        sidecar = report_path.parent / f"report-{artifact_id}.json"
        atomic_write_text(
            sidecar,
            json.dumps(report_json, ensure_ascii=False),
            root=paths.sessions_root,
        )
        report_json_path = str(sidecar)
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
        normalized_storage_path=report_json_path,
    )
    await artifacts.create(artifact)
    return artifact_id


async def submit_research_report_impl(
    *,
    store: EvidenceStore,
    artifacts: ArtifactRepository,
    paths: WorkspacePaths,
    session_id: UUID,
    run_id: UUID,
    report_data: dict,
    display_name: str | None = None,
    system_generated: bool = False,
) -> dict:
    records = await store.list_for_run()
    evidence_by_id = {item.id: item for item in records}
    degrade_reason: str | None = None

    try:
        report = ResearchReport.model_validate(report_data)
        # \u7cfb\u7edf\u6784\u9020\u7684\u964d\u7ea7\u62a5\u544a\uff08executor \u7b2c\u4e00\u6b21\u964d\u7ea7\u4ea7\u7269\uff09\u662f\u786e\u5b9a\u6027\u6570\u636e\uff0c
        # \u4e0d\u5c5e\u4e8e\u6a21\u578b\u81ea\u7531\u8f93\u51fa\uff0c\u4e0d\u9002\u7528\u8bc1\u636e\u6821\u9a8c\uff0c\u8df3\u8fc7\u5168\u90e8\u8de8\u6821\u9a8c\uff08summary \u6ce8\u518c\u8868 + claim/disagreement \u5f15\u7528 + high-confidence \u4fee\u590d\uff09\u3002
        if not system_generated:
            claims = collect_claims(report)

            for claim_id in report.executive_summary_claim_ids:
                if claim_id not in claims:
                    raise ReportValidationError(f"unknown summary claim: {claim_id}")

            for claim in claims.values():
                if not claim.citation_ids:
                    raise ReportValidationError(f"claim {claim.id} missing citations")
                unknown = [cid for cid in claim.citation_ids if cid not in evidence_by_id]
                if unknown:
                    raise ReportValidationError(
                        f"claim {claim.id} references unknown evidence: {unknown}"
                    )
                if len(set(claim.citation_ids)) != len(claim.citation_ids):
                    raise ReportValidationError(
                        f"claim {claim.id} references the same evidence more than once"
                    )

            for disagreement in report.disagreements:
                side_ids = [cid for side in disagreement.sides for cid in side.citation_ids]
                unknown = [cid for cid in side_ids if cid not in evidence_by_id]
                if unknown:
                    raise ReportValidationError(
                        f"disagreement '{disagreement.topic}' references unknown evidence: {unknown}"
                    )

            # Issue 6a: \u5f31\u8bc1\u636e\u4e0b\u7684 high confidence \u81ea\u52a8\u964d\u7ea7\uff08\u53ef\u964d\u7ea7\uff09\uff0c\u4e0d\u518d\u6574\u4efd\u5931\u8d25\u3002
            report, _healed_ids = _heal_high_confidence(report, evidence_by_id)
    except (ValidationError, ReportValidationError) as exc:
        degrade_reason = f"{type(exc).__name__}: {exc}"
        report = None

    if report is not None:
        markdown = render_report_markdown(
            report, collect_claims(report), evidence_by_id=evidence_by_id
        )
        title = report.title
        degraded = False
    else:
        markdown = _render_degraded_markdown(report_data, degrade_reason or "unknown")
        title = _DEGRADED_TITLE
        degraded = True

    artifact_id = await _save_report_artifact(
        artifacts=artifacts,
        paths=paths,
        session_id=session_id,
        run_id=run_id,
        markdown=markdown,
        title=title,
        display_name=display_name,
        report_json=(
            report.model_dump(mode="json")
            if report is not None and not system_generated
            else None
        ),
    )
    return {
        "artifact_id": str(artifact_id),
        "title": title,
        "degraded": degraded,
        "reason": degrade_reason,
    }
