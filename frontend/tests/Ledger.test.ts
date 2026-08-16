import { describe, expect, it } from "vitest";
import {
  countSourcesByType,
  partitionSourcesByCited,
} from "../src/components/Ledger";
import type { RunViewState } from "../src/state/runEventReducer";

type Source = RunViewState["sources"][number];

function source(partial: Partial<Source>): Source {
  return {
    evidence_id: "S1",
    source_type: "web",
    title: "",
    evidence_level: "search_snippet",
    ...partial,
  };
}

describe("countSourcesByType", () => {
  it("counts by source_type: web→S, document→D, knowledge_base→K", () => {
    const sources: Source[] = [
      source({ evidence_id: "S1", source_type: "web" }),
      source({ evidence_id: "S2", source_type: "web" }),
      source({ evidence_id: "D1", source_type: "document", evidence_level: "user_document" }),
      source({ evidence_id: "K1", source_type: "knowledge_base", evidence_level: "first_party" }),
    ];
    expect(countSourcesByType(sources)).toEqual({ S: 2, D: 1, K: 1 });
  });

  it("ignores evidence_level when counting (bug #26 regression)", () => {
    // 旧实现按 evidence_level 统计 s/d/k，这些值恒不等于 s/d/k，导致计数为 0。
    const sources: Source[] = [
      source({ evidence_id: "S1", source_type: "web", evidence_level: "search_snippet" }),
      source({ evidence_id: "K1", source_type: "knowledge_base", evidence_level: "first_party" }),
    ];
    const counts = countSourcesByType(sources);
    expect(counts.S).toBe(1);
    expect(counts.K).toBe(1);
    expect(counts.D).toBe(0);
  });

  it("returns zeros for empty sources", () => {
    expect(countSourcesByType([])).toEqual({ S: 0, D: 0, K: 0 });
  });
});

describe("partitionSourcesByCited (批次D)", () => {
  const sources: Source[] = [
    source({ evidence_id: "S1", source_type: "web" }),
    source({ evidence_id: "S2", source_type: "web" }),
    source({ evidence_id: "D1", source_type: "document", evidence_level: "user_document" }),
    source({ evidence_id: "K1", source_type: "knowledge_base", evidence_level: "first_party" }),
  ];

  it("empty/null/undefined citedIds → full fallback (citedMode=false)", () => {
    expect(partitionSourcesByCited(sources, [])).toEqual({
      main: sources,
      uncited: [],
      citedMode: false,
    });
    expect(partitionSourcesByCited(sources, null).citedMode).toBe(false);
    expect(partitionSourcesByCited(sources, undefined).main).toBe(sources);
  });

  it("main = cited subset ordered by citedIds (report [n] order)", () => {
    const p = partitionSourcesByCited(sources, ["K1", "S1"]);
    expect(p.citedMode).toBe(true);
    expect(p.main.map((s) => s.evidence_id)).toEqual(["K1", "S1"]);
    expect(p.uncited.map((s) => s.evidence_id)).toEqual(["S2", "D1"]);
  });

  it("cited id absent from sources is skipped; duplicates collapse", () => {
    const p = partitionSourcesByCited(sources, ["S1", "GHOST", "S1", "K1"]);
    expect(p.main.map((s) => s.evidence_id)).toEqual(["S1", "K1"]);
    expect(p.uncited.map((s) => s.evidence_id)).toEqual(["S2", "D1"]);
  });

  it("all sources cited → uncited empty, main in cited order", () => {
    const p = partitionSourcesByCited(sources, ["S1", "S2", "D1", "K1"]);
    expect(p.main.map((s) => s.evidence_id)).toEqual(["S1", "S2", "D1", "K1"]);
    expect(p.uncited).toEqual([]);
  });
});
