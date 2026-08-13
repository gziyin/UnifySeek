from __future__ import annotations

from pathlib import Path

from ai_dev_researcher.core.security import ensure_within_root
from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.reports import (
    ReportSection,
    ReportTable,
    ResearchClaim,
    ResearchReport,
)


def _build_numbering(
    report: ResearchReport, claims_by_id: dict[str, ResearchClaim]
) -> dict[str, int]:
    """按渲染顺序（执行摘要 → sections 深度优先含 table → 资料冲突 → 行动建议）
    首次出现顺序遍历全部 citation_ids，建立 evidence_id → 1-based 编号映射（去重）。"""
    order: list[str] = []
    seen: set[str] = set()

    def add(cids: list[str]) -> None:
        for cid in cids:
            if cid not in seen:
                seen.add(cid)
                order.append(cid)

    for claim_id in report.executive_summary_claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is not None:
            add(claim.citation_ids)

    def walk_section(sec: ReportSection) -> None:
        for claim in sec.claims:
            add(claim.citation_ids)
        if sec.table is not None:
            add(sec.table.citation_ids)
        for sub in sec.subsections:
            walk_section(sub)

    for sec in report.sections:
        walk_section(sec)

    for item in report.disagreements:
        for side in item.sides:
            add(side.citation_ids)

    for claim in report.recommendations:
        add(claim.citation_ids)

    return {cid: i + 1 for i, cid in enumerate(order)}


def _render_claim_paragraph(
    claim: ResearchClaim, numbering: dict[str, int]
) -> str:
    """段落式 claim：statement（可含粗体）原样输出，后附 [n] 编号引用。"""
    cites = "".join(
        f"[{numbering[c]}]" for c in claim.citation_ids if c in numbering
    )
    return f"{claim.statement}{cites}"


def _render_table(table: ReportTable, numbering: dict[str, int]) -> list[str]:
    out: list[str] = [
        "| " + " | ".join(table.columns) + " |",
        "|" + "|".join(["---"] * len(table.columns)) + "|",
    ]
    for row in table.rows:
        out.append("| " + " | ".join(row) + " |")
    cites = "".join(f"[{numbering[c]}]" for c in table.citation_ids if c in numbering)
    if cites:
        out.append("")
        out.append(f"*来源：{cites}*")
    return out


def _render_section(
    lines: list[str], sec: ReportSection, numbering: dict[str, int], level: int
) -> None:
    lines.append(f"{'#' * level} {sec.heading}")
    lines.append("")
    for claim in sec.claims:
        lines.append(_render_claim_paragraph(claim, numbering))
    lines.append("")
    if sec.table is not None:
        lines.extend(_render_table(sec.table, numbering))
        lines.append("")
    for sub in sec.subsections:
        _render_section(lines, sub, numbering, level + 1)


def render_report_markdown(
    report: ResearchReport,
    claims_by_id: dict[str, ResearchClaim],
    evidence_by_id: dict[str, EvidenceRecord] | None = None,
) -> str:
    numbering = _build_numbering(report, claims_by_id)
    lines: list[str] = [f"# {report.title}", ""]

    lines.append("## 执行摘要")
    lines.append("")
    for claim_id in report.executive_summary_claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is not None:
            lines.append(_render_claim_paragraph(claim, numbering))
    lines.append("")

    for sec in report.sections:
        _render_section(lines, sec, numbering, level=2)

    if report.disagreements:
        lines.append("## 资料冲突")
        lines.append("")
        for item in report.disagreements:
            lines.append(f"### {item.topic}")
            lines.append("")
            for side in item.sides:
                cites = "".join(
                    f"[{numbering[c]}]" for c in side.citation_ids if c in numbering
                )
                lines.append(f"- {side.position}{cites}")
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
            lines.append(_render_claim_paragraph(claim, numbering))
        lines.append("")

    if evidence_by_id and numbering:
        lines.append("### Sources")
        lines.append("")
        for cid, n in numbering.items():
            evidence = evidence_by_id.get(cid)
            url = (
                (evidence.canonical_url or evidence.locator)
                if evidence is not None
                else ""
            )
            if url:
                lines.append(f"- [{n}] {url}")
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

    def add_claim(claim: ResearchClaim) -> None:
        claims[claim.id] = claim

    def walk_section(sec: ReportSection) -> None:
        for claim in sec.claims:
            add_claim(claim)
        for sub in sec.subsections:
            walk_section(sub)

    for section in report.sections:
        walk_section(section)
    for claim in report.recommendations:
        add_claim(claim)
    return claims
