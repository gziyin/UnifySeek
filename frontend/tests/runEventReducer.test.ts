import { describe, expect, it } from "vitest";
import { initialRunViewState, runEventReducer } from "../src/state/runEventReducer";
import type { ResearchEvent } from "../src/domain/schemas";

function makeEvent(type: string, payload: Record<string, unknown>, seq: number): ResearchEvent {
  return {
    protocol_version: "1.0",
    event_id: `evt-${seq}`,
    seq,
    session_id: "s1",
    run_id: "r1",
    type,
    occurred_at: "2026-08-03T00:00:00Z",
    actor: "system",
    payload,
  };
}

describe("runEventReducer sources", () => {
  it("captures url/query for web sources", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent(
          "source.discovered",
          {
            evidence_id: "S1",
            source_type: "web",
            title: "DeepAgents docs",
            evidence_level: "search_snippet",
            url: "https://example.com/docs",
            query: "deepagents architecture",
            publisher_key: "example.com",
          },
          1,
        ),
      ],
    });
    expect(state.sources).toHaveLength(1);
    expect(state.sources[0]).toMatchObject({
      evidence_id: "S1",
      url: "https://example.com/docs",
      query: "deepagents architecture",
    });
  });

  it("captures path/line range for knowledge_base sources", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent(
          "evidence.recorded",
          {
            evidence_id: "K1",
            source_type: "knowledge_base",
            locator: "kb:deepagents-0.6.2/x.py lines 1-2",
            line_start: 1,
            line_end: 2,
            excerpt: "def x(): pass",
          },
          1,
        ),
        makeEvent(
          "source.discovered",
          {
            evidence_id: "K1",
            source_type: "knowledge_base",
            title: "x.py",
            evidence_level: "first_party",
            path: "deepagents-0.6.2/x.py",
          },
          2,
        ),
      ],
    });
    const kb = state.sources.find((s) => s.evidence_id === "K1");
    expect(kb).toBeDefined();
    expect(kb?.path).toBe("deepagents-0.6.2/x.py");
  });

  it("deduplicates by seq", () => {
    const first = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1)],
    });
    const state = runEventReducer(first, {
      type: "events",
      events: [makeEvent("run.started", {}, 1), makeEvent("report.ready", { artifact_id: "a1" }, 2)],
    });
    expect(state.events).toHaveLength(2);
    expect(state.reportArtifactId).toBe("a1");
  });

  it("reset clears state", () => {
    const filled = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("source.discovered", { evidence_id: "S1" }, 1)],
    });
    const state = runEventReducer(filled, { type: "reset" });
    expect(state.sources).toHaveLength(0);
    expect(state.events).toHaveLength(0);
  });
});
