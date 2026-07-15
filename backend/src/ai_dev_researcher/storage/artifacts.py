from __future__ import annotations

from pathlib import Path

from ai_dev_researcher.core.security import ensure_within_root
from ai_dev_researcher.domain.reports import ResearchClaim, ResearchReport


def render_report_markdown(report: ResearchReport, claims_by_id: dict[str, ResearchClaim]) -> str:
    lines: list[str] = [f"# {report.title}", ""]
    lines.append("## 执行摘要")
    lines.append("")
    for claim_id in report.executive_summary_claim_ids:
        claim = claims_by_id[claim_id]
        cites = ", ".join(f"`{cid}`" for cid in claim.citation_ids)
        lines.append(f"- {claim.statement} ({cites}; confidence={claim.confidence})")
    lines.append("")

    for section in report.sections:
        lines.append(f"## {section.heading}")
        lines.append("")
        for claim in section.claims:
            cites = ", ".join(f"`{cid}`" for cid in claim.citation_ids)
            lines.append(f"- {claim.statement} ({cites}; confidence={claim.confidence})")
        lines.append("")

    if report.disagreements:
        lines.append("## 资料冲突")
        lines.append("")
        for item in report.disagreements:
            lines.append(f"### {item.topic}")
            for side in item.sides:
                cites = ", ".join(f"`{cid}`" for cid in side.citation_ids)
                lines.append(f"- {side.position} ({cites})")
            lines.append("")

    if report.unknowns:
        lines.append("## 未知项")
        lines.append("")
        for unknown in report.unknowns:
            lines.append(f"- {unknown}")
        lines.append("")

    if report.recommendations:
        lines.append("## 行动建议")
        lines.append("")
        for claim in report.recommendations:
            cites = ", ".join(f"`{cid}`" for cid in claim.citation_ids)
            lines.append(f"- {claim.statement} ({cites}; confidence={claim.confidence})")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def atomic_write_text(target: Path, content: str, *, root: Path) -> Path:
    ensured = ensure_within_root(target, root)
    ensured.parent.mkdir(parents=True, exist_ok=True)
    tmp = ensured.with_suffix(ensured.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(ensured)
    return ensured


def collect_claims(report: ResearchReport) -> dict[str, ResearchClaim]:
    claims: dict[str, ResearchClaim] = {}
    for section in report.sections:
        for claim in section.claims:
            claims[claim.id] = claim
    for claim in report.recommendations:
        claims[claim.id] = claim
    return claims
