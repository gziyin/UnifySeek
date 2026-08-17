import { describe, expect, it } from "vitest";
import { extractCitedEvidenceIds } from "../src/domain/reportCites";
import type { ResearchReport } from "../src/domain/schemas";

function claim(
  id: string,
  citation_ids: string[],
): { id: string; statement: string; citation_ids: string[]; confidence: "high" } {
  return { id, statement: `claim ${id}`, citation_ids, confidence: "high" };
}

/**
 * 构造覆盖 exec_summary（含缺失 claim）、sections 深度优先（claims→table→subsections）、
 * disagreements、recommendations、重复引用去重的完整报告。
 * 期望顺序严格镜像 backend storage/artifacts.py `_build_numbering`。
 */
const fullReport: ResearchReport = {
  title: "T",
  executive_summary_claim_ids: ["c-missing", "c-a", "c-b"],
  sections: [
    {
      heading: "S1",
      claims: [claim("c-a", ["S1"])],
      table: { columns: ["col"], rows: [], citation_ids: ["S4"] },
      subsections: [
        {
          heading: "S1.1",
          claims: [claim("c-c", ["S5"])],
          table: null,
          subsections: [
            {
              heading: "S1.1.1",
              claims: [claim("c-d", ["S2", "S6"])],
              table: null,
              subsections: [],
            },
          ],
        },
      ],
    },
    {
      heading: "S2",
      claims: [claim("c-b", ["S2", "S3"])],
      table: null,
      subsections: [],
    },
  ],
  disagreements: [
    {
      topic: "d1",
      claim_ids: ["c-b"],
      sides: [
        { position: "left", citation_ids: ["S3", "S7"] },
        { position: "right", citation_ids: ["S8"] },
      ],
    },
  ],
  unknowns: [],
  recommendations: [claim("c-rec", ["S6", "S9"])],
};

describe("extractCitedEvidenceIds (批次D)", () => {
  it("summary_claims take priority and are ordered before sections", () => {
    const report: ResearchReport = {
      title: "T",
      summary_claims: [claim("sum-a", ["S10", "S0"]), claim("sum-b", ["S0", "S11"])],
      executive_summary_claim_ids: ["c-a"],
      sections: [
        {
          heading: "S1",
          claims: [claim("c-a", ["S1"])],
          table: null,
          subsections: [],
        },
      ],
      disagreements: [],
      unknowns: [],
      recommendations: [
        claim("c-rec", ["S2"]),
        claim("c-rec2", ["S12"]),
      ],
    };
    // summary 优先：S10,S0,S11（去重）→ 再 sections(S1) → disagreements → recommendations(S2,S12)
    expect(extractCitedEvidenceIds(report)).toEqual([
      "S10",
      "S0",
      "S11",
      "S1",
      "S2",
      "S12",
    ]);
  });

  it("distilled summary_claims alone (empty exec ids) drives order", () => {
    const report: ResearchReport = {
      title: "T",
      summary_claims: [claim("sum1", ["K1"]), claim("sum2", ["K2", "K1"])],
      executive_summary_claim_ids: [],
      sections: [],
      disagreements: [],
      unknowns: [],
      recommendations: [],
    };
    expect(extractCitedEvidenceIds(report)).toEqual(["K1", "K2"]);
  });

  it("mirrors backend _build_numbering traversal order", () => {
    // exec_summary(c-missing 缺失跳过 → S1 → S2,S3) → sections 深先(table S4 → subs S5 → S2,S6)
    // → disagreements(S3 已见,S7,S8) → recommendations(S6 已见,S9)
    expect(extractCitedEvidenceIds(fullReport)).toEqual([
      "S1",
      "S2",
      "S3",
      "S4",
      "S5",
      "S6",
      "S7",
      "S8",
      "S9",
    ]);
  });

  it("exec_summary referencing a nonexistent claim id is skipped without crashing", () => {
    const report: ResearchReport = {
      title: "T",
      executive_summary_claim_ids: ["ghost"],
      sections: [],
      disagreements: [],
      unknowns: [],
      recommendations: [],
    };
    expect(extractCitedEvidenceIds(report)).toEqual([]);
  });

  it("dedupes repeated citations (first occurrence wins)", () => {
    const report: ResearchReport = {
      title: "T",
      executive_summary_claim_ids: [],
      sections: [
        {
          heading: "a",
          claims: [claim("c1", ["S1", "S2"])],
          table: null,
          subsections: [],
        },
        {
          heading: "b",
          claims: [claim("c2", ["S2", "S3"])],
          table: null,
          subsections: [],
        },
      ],
      disagreements: [],
      unknowns: [],
      recommendations: [],
    };
    expect(extractCitedEvidenceIds(report)).toEqual(["S1", "S2", "S3"]);
  });

  it("resolves exec_summary claims that live in recommendations", () => {
    const report: ResearchReport = {
      title: "T",
      executive_summary_claim_ids: ["c-r"],
      sections: [],
      disagreements: [],
      unknowns: [],
      recommendations: [claim("c-r", ["K1"])],
    };
    expect(extractCitedEvidenceIds(report)).toEqual(["K1"]);
  });

  it("table citation_ids are ordered right after the section's claims", () => {
    const report: ResearchReport = {
      title: "T",
      executive_summary_claim_ids: [],
      sections: [
        {
          heading: "a",
          claims: [claim("c1", ["S1"])],
          table: { columns: ["c"], rows: [], citation_ids: ["S2"] },
          subsections: [
            { heading: "a1", claims: [claim("c2", ["S3"])], table: null, subsections: [] },
          ],
        },
      ],
      disagreements: [],
      unknowns: [],
      recommendations: [],
    };
    expect(extractCitedEvidenceIds(report)).toEqual(["S1", "S2", "S3"]);
  });
});
