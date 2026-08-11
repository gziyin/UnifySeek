import { afterEach, describe, expect, it, vi } from "vitest";
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

afterEach(() => {
  vi.restoreAllMocks();
});

describe("runEventReducer phases (issue #27)", () => {
  it("run.started activates plan phase and starts total timer", () => {
    vi.spyOn(performance, "now").mockReturnValue(1000);
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1)],
    });
    expect(state.phases.map((p) => p.status)).toEqual(["active", "pending", "pending"]);
    expect(state.totalStartedAt).not.toBeNull();
    expect(state.runFinished).toBe(false);
  });

  it("research events close plan and activate research", () => {
    vi.spyOn(performance, "now").mockReturnValue(1000);
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent("run.started", {}, 1),
        makeEvent("tool.started", { tool_name: "search_web" }, 2),
        makeEvent("source.discovered", { evidence_id: "S1", source_type: "web" }, 3),
      ],
    });
    expect(state.phases.map((p) => p.status)).toEqual(["done", "active", "pending"]);
  });

  it("submit_research_report / report.ready activates report phase and closes research", () => {
    vi.spyOn(performance, "now").mockReturnValue(1000);
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent("run.started", {}, 1),
        makeEvent("tool.started", { tool_name: "search_web" }, 2),
        makeEvent(
          "tool.completed",
          { tool_name: "submit_research_report", artifact_id: "a1" },
          3,
        ),
        makeEvent("report.ready", { artifact_id: "a1", degraded: false }, 4),
      ],
    });
    expect(state.phases.map((p) => p.status)).toEqual(["done", "done", "active"]);
  });

  it("run.succeeded freezes all phases and total elapsed (across batches)", () => {
    vi.spyOn(performance, "now").mockReturnValue(1000);
    let state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1)],
    });
    vi.spyOn(performance, "now").mockReturnValue(5000);
    state = runEventReducer(state, {
      type: "events",
      events: [
        makeEvent("tool.started", { tool_name: "search_web" }, 2),
        makeEvent("report.ready", { artifact_id: "a1" }, 3),
        makeEvent("run.succeeded", { report_artifact_id: "a1" }, 4),
      ],
    });
    expect(state.runFinished).toBe(true);
    expect(state.phases.every((p) => p.status === "done")).toBe(true);
    expect(state.totalElapsedMs).toBe(4000); // 5000 - 1000
  });

  it("run.cancelled also freezes phases (terminal)", () => {
    vi.spyOn(performance, "now").mockReturnValue(1000);
    let state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1)],
    });
    vi.spyOn(performance, "now").mockReturnValue(2500);
    state = runEventReducer(state, {
      type: "events",
      events: [makeEvent("run.cancelled", { reason: "user_cancelled" }, 2)],
    });
    expect(state.runFinished).toBe(true);
    expect(state.totalElapsedMs).toBe(1500);
  });

  it("reconnecting catch-up does not regress an already-done phase (only advances)", () => {
    vi.spyOn(performance, "now").mockReturnValue(1000);
    let state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1)],
    });
    // 第二批补齐 research 事件（模拟 after_seq 补齐）
    state = runEventReducer(state, {
      type: "events",
      events: [makeEvent("tool.started", { tool_name: "extract_web_sources" }, 2)],
    });
    // 第三批到达迟到的 plan.updated（已 seen 之前？不，seq3 新）→ 不应回退 research
    state = runEventReducer(state, {
      type: "events",
      events: [makeEvent("plan.updated", { items: [{ id: "a", content: "x", status: "done" }] }, 3)],
    });
    expect(state.phases[1].status).toBe("active"); // research 仍是 active
    expect(state.phases[0].status).toBe("done");
  });
});
