import type { ResearchClaim, ResearchReport, ReportSection } from "./schemas";

/**
 * 收集「claim id → claim」注册表：sections（递归含 subsections）claims + recommendations，
 * 与 backend storage/artifacts.py `collect_claims` 一致（disagreements 只引用 claim id，不新增 claim）。
 */
function collectClaims(report: ResearchReport): Map<string, ResearchClaim> {
  const registry = new Map<string, ResearchClaim>();
  const add = (claim: ResearchClaim) => {
    registry.set(claim.id, claim);
  };
  const walkSection = (sec: ReportSection) => {
    for (const claim of sec.claims) add(claim);
    for (const sub of sec.subsections ?? []) walkSection(sub);
  };
  for (const claim of report.summary_claims ?? []) add(claim);
  for (const section of report.sections) walkSection(section);
  for (const claim of report.recommendations ?? []) add(claim);
  return registry;
}

/**
 * 从结构化报告提取「被引用证据」的有序 evidence_id 列表（首次出现去重）。
 *
 * 必须镜像 backend storage/artifacts.py `_build_numbering` 的遍历顺序：
 * 1. summary_claims（新结构蒸馏结论，非空则优先）→ 否则回退 executive_summary_claim_ids
 *    （经注册表解析 citation_ids，缺失 claim 则跳过）
 * 2. sections 深度优先：claims → table.citation_ids → subsections
 * 3. disagreements 各 side
 * 4. recommendations
 *
 * 返回顺序即报告 `### Sources` 的 [n] 编号顺序，供账本主视图与报告严格对齐。
 */
export function extractCitedEvidenceIds(report: ResearchReport): string[] {
  const registry = collectClaims(report);
  const order: string[] = [];
  const seen = new Set<string>();
  const add = (cids: string[]) => {
    for (const cid of cids) {
      if (!seen.has(cid)) {
        seen.add(cid);
        order.push(cid);
      }
    }
  };

  if ((report.summary_claims ?? []).length) {
    for (const claim of report.summary_claims ?? []) add(claim.citation_ids);
  } else {
    for (const claimId of report.executive_summary_claim_ids) {
      const claim = registry.get(claimId);
      if (claim) add(claim.citation_ids);
    }
  }

  const walkSection = (sec: ReportSection) => {
    for (const claim of sec.claims) add(claim.citation_ids);
    if (sec.table) add(sec.table.citation_ids);
    for (const sub of sec.subsections ?? []) walkSection(sub);
  };
  for (const section of report.sections) walkSection(section);

  for (const item of report.disagreements ?? []) {
    for (const side of item.sides) add(side.citation_ids);
  }

  for (const claim of report.recommendations ?? []) add(claim.citation_ids);

  return order;
}
