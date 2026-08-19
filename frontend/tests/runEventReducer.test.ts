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

describe("runEventReducer source upsert by evidence_id (batch B ledger dedupe)", () => {
  it("repeated K within one dispatch appears once with merged fields", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent(
          "source.discovered",
          { evidence_id: "K1", source_type: "knowledge_base", title: "a", url: "u1" },
          1,
        ),
        makeEvent(
          "source.discovered",
          { evidence_id: "K1", source_type: "knowledge_base", title: "b", url: "" },
          2,
        ),
        makeEvent(
          "source.discovered",
          { evidence_id: "K1", source_type: "knowledge_base", title: "c" },
          3,
        ),
      ],
    });
    expect(state.sources).toHaveLength(1);
    expect(state.sources[0]).toMatchObject({
      evidence_id: "K1",
      title: "c",
      url: "u1",
    });
  });

  it("repeats across dispatches collapse to one entry, first position kept", () => {
    const first = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent(
          "source.discovered",
          { evidence_id: "K1", source_type: "knowledge_base", title: "t" },
          1,
        ),
        makeEvent("source.discovered", { evidence_id: "S1", source_type: "web", url: "u" }, 2),
      ],
    });
    const state = runEventReducer(first, {
      type: "events",
      events: [
        makeEvent(
          "source.discovered",
          { evidence_id: "K1", source_type: "knowledge_base", title: "t-updated" },
          3,
        ),
        makeEvent("source.discovered", { evidence_id: "S2", source_type: "web" }, 4),
      ],
    });
    expect(state.sources.map((s) => s.evidence_id)).toEqual(["K1", "S1", "S2"]);
    expect(state.sources[0].title).toBe("t-updated");
  });

  it("ignores source.discovered without a non-empty evidence_id", () => {
    const noId = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("source.discovered", { title: "orphan" }, 1)],
    });
    expect(noId.sources).toHaveLength(0);

    const emptyId = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("source.discovered", { evidence_id: "", title: "orphan" }, 1)],
    });
    expect(emptyId.sources).toHaveLength(0);
  });

  it("empty later fields never erase existing values", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent(
          "source.discovered",
          {
            evidence_id: "K1",
            source_type: "knowledge_base",
            title: "x.py",
            path: "deepagents-0.6.2/x.py",
            line_start: 1,
            line_end: 2,
          },
          1,
        ),
        makeEvent(
          "source.discovered",
          {
            evidence_id: "K1",
            source_type: "",
            title: "",
            path: null,
            line_start: null,
            line_end: null,
          },
          2,
        ),
      ],
    });
    expect(state.sources).toHaveLength(1);
    expect(state.sources[0]).toMatchObject({
      source_type: "knowledge_base",
      title: "x.py",
      path: "deepagents-0.6.2/x.py",
      line_start: 1,
      line_end: 2,
    });
  });

  it("non-empty later fields fill missing previous fields in place", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent(
          "source.discovered",
          { evidence_id: "K1", source_type: "knowledge_base", title: "t" },
          1,
        ),
        makeEvent(
          "source.discovered",
          {
            evidence_id: "K1",
            source_type: "knowledge_base",
            title: "t",
            excerpt: "def x(): pass",
            query: "kb search",
          },
          2,
        ),
      ],
    });
    expect(state.sources).toHaveLength(1);
    expect(state.sources[0]).toMatchObject({
      evidence_id: "K1",
      excerpt: "def x(): pass",
      query: "kb search",
    });
  });
});

describe("runEventReducer degraded consumption", () => {
  it("sets reportDegraded true and keeps reason from report.ready", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent(
          "report.ready",
          { artifact_id: "a1", degraded: true, reason: "bad citations" },
          1,
        ),
      ],
    });
    expect(state.reportDegraded).toBe(true);
    expect(state.reportReason).toBe("bad citations");
    expect(state.reportArtifactId).toBe("a1");
  });

  it("sets reportDegraded false when report.ready carries degraded false", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("report.ready", { artifact_id: "a1", degraded: false }, 1)],
    });
    expect(state.reportDegraded).toBe(false);
    expect(state.reportArtifactId).toBe("a1");
  });

  it("defaults reportDegraded to false when degraded field is absent", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("report.ready", { artifact_id: "a1" }, 1)],
    });
    expect(state.reportDegraded).toBe(false);
    expect(state.reportReason).toBeUndefined();
  });

  it("captures degrade reason from submit_research_report tool.completed (real backend path)", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent("report.ready", { artifact_id: "a1", degraded: true }, 1),
        makeEvent(
          "tool.completed",
          {
            tool_name: "submit_research_report",
            artifact_id: "a1",
            degraded: true,
            reason: "ReportValidationError: missing evidence",
          },
          2,
        ),
      ],
    });
    expect(state.reportDegraded).toBe(true);
    expect(state.reportReason).toBe("ReportValidationError: missing evidence");
  });

  it("keeps prior degraded state when run.succeeded arrives without degraded field", () => {
    const degraded = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("report.ready", { artifact_id: "a1", degraded: true }, 1)],
    });
    const state = runEventReducer(degraded, {
      type: "events",
      events: [makeEvent("run.succeeded", { report_artifact_id: "a1" }, 2)],
    });
    expect(state.reportDegraded).toBe(true);
    expect(state.reportArtifactId).toBe("a1");
  });

  it("reset clears degraded flags", () => {
    const degraded = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent(
          "report.ready",
          { artifact_id: "a1", degraded: true, reason: "bad citations" },
          1,
        ),
      ],
    });
    const state = runEventReducer(degraded, { type: "reset" });
    expect(state.reportDegraded).toBe(false);
    expect(state.reportReason).toBeUndefined();
    expect(state.reportArtifactId).toBeUndefined();
  });
});
