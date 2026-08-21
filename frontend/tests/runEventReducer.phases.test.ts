import { describe, expect, it } from "vitest";
import { initialRunViewState, runEventReducer } from "../src/state/runEventReducer";
import type { ResearchEvent } from "../src/domain/schemas";

/**
 * 各事件带不同的 occurred_at（epoch 时间戳），计时由事件时间戳驱动，而非共享的
 * performance.now()。这覆盖 #34：hydrate 批量灌入同一次 dispatch 的多个事件时，
 * 前一阶段 elapsed 不应被后一阶段清零。
 */
function makeEvent(
  type: string,
  payload: Record<string, unknown>,
  seq: number,
  occurredAt = "2026-08-03T00:00:10Z",
): ResearchEvent {
  return {
    protocol_version: "1.0",
    event_id: `evt-${seq}`,
    seq,
    session_id: "s1",
    run_id: "r1",
    type,
    occurred_at: occurredAt,
    actor: "system",
    payload,
  };
}

const T10 = Date.parse("2026-08-03T00:00:10Z");
const T40 = Date.parse("2026-08-03T00:00:40Z");
const T70 = Date.parse("2026-08-03T00:01:10Z");
const T100 = Date.parse("2026-08-03T00:01:40Z");

describe("runEventReducer phases (issue #27 / #34)", () => {
  it("poll terminal sync freezes an active phase when the terminal event was missed", () => {
    const running = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z"),
        makeEvent("tool.started", { tool_name: "search_web" }, 2, "2026-08-03T00:00:40Z"),
      ],
    });

    const terminal = runEventReducer(running, {
      type: "terminalSync",
      status: "succeeded",
      at: T100,
    });

    expect(terminal.runFinished).toBe(true);
    expect(terminal.terminalStatus).toBe("succeeded");
    expect(terminal.phases.map((phase) => phase.status)).toEqual(["done", "done", "pending"]);
    expect(terminal.phases[1].elapsedMs).toBe(T100 - T40);
    expect(terminal.totalElapsedMs).toBe(T100 - T10);
  });

  it("run.started activates plan phase and starts total timer at its event ts", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1)],
    });
    expect(state.phases.map((p) => p.status)).toEqual(["active", "pending", "pending"]);
    expect(state.totalStartedAt).toBe(T10);
    expect(state.phases[0].startedAt).toBe(T10);
    expect(state.runFinished).toBe(false);
  });

  it("research events close plan (elapsed from event ts) and activate research", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z"),
        makeEvent("tool.started", { tool_name: "search_web" }, 2, "2026-08-03T00:00:40Z"),
        makeEvent("source.discovered", { evidence_id: "S1", source_type: "web" }, 3, "2026-08-03T00:00:40Z"),
      ],
    });
    expect(state.phases.map((p) => p.status)).toEqual(["done", "active", "pending"]);
    // 规划阶段 10s→40s = 30s
    expect(state.phases[0].elapsedMs).toBe(T40 - T10);
  });

  it("#34 hydrate batch: plan elapsed is NOT reset by later phases in the same dispatch", () => {
    // 一次 dispatch 灌入整批事件（模拟 listEvents hydrate），各事件时间戳不同。
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z"),
        makeEvent("tool.started", { tool_name: "search_web" }, 2, "2026-08-03T00:00:40Z"),
        makeEvent(
          "tool.completed",
          { tool_name: "submit_research_report", artifact_id: "a1" },
          3,
          "2026-08-03T00:01:10Z",
        ),
        makeEvent("report.ready", { artifact_id: "a1", degraded: false }, 4, "2026-08-03T00:01:10Z"),
        makeEvent("run.succeeded", { report_artifact_id: "a1" }, 5, "2026-08-03T00:01:40Z"),
      ],
    });
    expect(state.runFinished).toBe(true);
    expect(state.phases.every((p) => p.status === "done")).toBe(true);
    // 各阶段耗时由事件时间戳差值得出，不被后续阶段清零
    expect(state.phases[0].elapsedMs).toBe(T40 - T10); // plan 10s→40s = 30s
    expect(state.phases[1].elapsedMs).toBe(T70 - T40); // research 40s→70s = 30s
    expect(state.phases[2].elapsedMs).toBe(T100 - T70); // report 70s→100s = 30s
    // 总耗时 = 末事件 ts − 起始 ts
    expect(state.totalElapsedMs).toBe(T100 - T10);
  });

  it("submit_research_report / report.ready activates report phase and closes research", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z"),
        makeEvent("tool.started", { tool_name: "search_web" }, 2, "2026-08-03T00:00:40Z"),
        makeEvent(
          "tool.completed",
          { tool_name: "submit_research_report", artifact_id: "a1" },
          3,
          "2026-08-03T00:01:10Z",
        ),
        makeEvent("report.ready", { artifact_id: "a1", degraded: false }, 4, "2026-08-03T00:01:10Z"),
      ],
    });
    expect(state.phases.map((p) => p.status)).toEqual(["done", "done", "active"]);
  });

  it("get_evidence_ledger completion starts report once without resetting on repeats", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z"),
        makeEvent("tool.started", { tool_name: "search_web" }, 2, "2026-08-03T00:00:40Z"),
        makeEvent("tool.completed", { tool_name: "get_evidence_ledger" }, 3, "2026-08-03T00:01:10Z"),
        makeEvent("tool.completed", { tool_name: "get_evidence_ledger" }, 4, "2026-08-03T00:01:20Z"),
        makeEvent("run.succeeded", {}, 5, "2026-08-03T00:01:40Z"),
      ],
    });
    expect(state.phases[2].startedAt).toBe(T70);
    expect(state.phases[2].elapsedMs).toBe(T100 - T70);
    expect(state.phases[1].elapsedMs).toBe(T70 - T40);
  });

  it("run.succeeded freezes all phases and total elapsed across batches", () => {
    let state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z")],
    });
    state = runEventReducer(state, {
      type: "events",
      events: [
        makeEvent("tool.started", { tool_name: "search_web" }, 2, "2026-08-03T00:00:40Z"),
        makeEvent("report.ready", { artifact_id: "a1" }, 3, "2026-08-03T00:01:10Z"),
        makeEvent("run.succeeded", { report_artifact_id: "a1" }, 4, "2026-08-03T00:01:40Z"),
      ],
    });
    expect(state.runFinished).toBe(true);
    expect(state.phases.every((p) => p.status === "done")).toBe(true);
    expect(state.totalElapsedMs).toBe(T100 - T10);
  });

  it("run.cancelled also freezes phases (terminal)", () => {
    let state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z")],
    });
    state = runEventReducer(state, {
      type: "events",
      events: [makeEvent("run.cancelled", { reason: "user_cancelled" }, 2, "2026-08-03T00:00:40Z")],
    });
    expect(state.runFinished).toBe(true);
    expect(state.totalElapsedMs).toBe(T40 - T10);
  });

  it("reconnecting catch-up does not regress an already-done phase (only advances)", () => {
    let state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z")],
    });
    // 第二批补齐 research 事件（模拟 after_seq 补齐）
    state = runEventReducer(state, {
      type: "events",
      events: [makeEvent("tool.started", { tool_name: "extract_web_sources" }, 2, "2026-08-03T00:00:40Z")],
    });
    // 第三批到达迟到的 plan.updated（seq3 新）→ 不应回退 research
    state = runEventReducer(state, {
      type: "events",
      events: [
        makeEvent(
          "plan.updated",
          { items: [{ id: "a", content: "x", status: "done" }] },
          3,
          "2026-08-03T00:00:40Z",
        ),
      ],
    });
    expect(state.phases[1].status).toBe("active"); // research 仍是 active
    expect(state.phases[0].status).toBe("done");
  });
});

describe("F1 optimisticStart (#41) — 提交即激活规划阶段", () => {
  it("activates plan + total timer at submit time", () => {
    const at = Date.now();
    const state = runEventReducer(initialRunViewState, { type: "optimisticStart", at });
    expect(state.phases[0].status).toBe("active");
    expect(state.phases[0].startedAt).toBe(at);
    expect(state.totalStartedAt).toBe(at);
    expect(state.phases[1].status).toBe("pending");
    expect(state.runFinished).toBe(false);
  });

  it("idempotent: second optimisticStart is a no-op", () => {
    const at1 = Date.now();
    const first = runEventReducer(initialRunViewState, { type: "optimisticStart", at: at1 });
    const state = runEventReducer(first, { type: "optimisticStart", at: at1 + 5000 });
    expect(state.phases[0].startedAt).toBe(at1);
    expect(state.totalStartedAt).toBe(at1);
  });

  it("real run.started does not overwrite optimistic startedAt / totalStartedAt", () => {
    const at = Date.parse("2026-08-03T00:00:10Z");
    const optimistic = runEventReducer(initialRunViewState, { type: "optimisticStart", at });
    const state = runEventReducer(optimistic, {
      type: "events",
      events: [makeEvent("run.started", {}, 1, "2026-08-03T00:00:15Z")],
    });
    expect(state.phases[0].status).toBe("active");
    expect(state.phases[0].startedAt).toBe(at); // 未被 15s 覆盖
    expect(state.totalStartedAt).toBe(at);
  });

  it("optimisticStart does nothing once run.started already arrived", () => {
    const base = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z")],
    });
    const state = runEventReducer(base, {
      type: "optimisticStart",
      at: Date.parse("2026-08-03T00:00:05Z"),
    });
    expect(state.phases[0].startedAt).toBe(T10);
    expect(state.totalStartedAt).toBe(T10);
  });

  it("optimistic total freezes consistently at terminal", () => {
    const at = Date.parse("2026-08-03T00:00:10Z");
    const optimistic = runEventReducer(initialRunViewState, { type: "optimisticStart", at });
    const state = runEventReducer(optimistic, {
      type: "events",
      events: [
        makeEvent("tool.started", { tool_name: "search_web" }, 2, "2026-08-03T00:00:40Z"),
        makeEvent("run.succeeded", { report_artifact_id: "a1" }, 3, "2026-08-03T00:01:40Z"),
      ],
    });
    expect(state.runFinished).toBe(true);
    expect(state.totalElapsedMs).toBe(T100 - at);
    expect(state.phases[0].elapsedMs).toBe(T40 - at); // plan = 提交→研究开始
  });
});

describe("F2 clock & replay (#42)", () => {
  it("clockSync stores client-server offset (now - serverTimeMs)", () => {
    const serverTimeMs = Date.parse("2026-08-03T00:00:10Z");
    const state = runEventReducer(initialRunViewState, { type: "clockSync", serverTimeMs });
    const expected = Date.now() - serverTimeMs;
    expect(state.clockOffsetMs).toBeGreaterThan(0);
    expect(Math.abs(state.clockOffsetMs - expected)).toBeLessThan(100);
  });

  it("tiny clockSync deltas are ignored (no re-render churn)", () => {
    const serverTimeMs = Date.parse("2026-08-03T00:00:10Z");
    const base = runEventReducer(initialRunViewState, { type: "clockSync", serverTimeMs });
    const state = runEventReducer(base, { type: "clockSync", serverTimeMs: serverTimeMs - 2 });
    expect(state.clockOffsetMs).toBe(base.clockOffsetMs);
  });

  it("replay of already-seen events does not recompute startedAt / totalElapsedMs", () => {
    let state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z")],
    });
    state = runEventReducer(state, {
      type: "events",
      events: [makeEvent("tool.started", { tool_name: "search_web" }, 2, "2026-08-03T00:00:40Z")],
    });
    state = runEventReducer(state, {
      type: "events",
      events: [makeEvent("run.succeeded", { report_artifact_id: "a1" }, 3, "2026-08-03T00:01:40Z")],
    });
    const snapshot = {
      startedAt: state.phases.map((p) => p.startedAt),
      statuses: state.phases.map((p) => p.status),
      elapsedMs: state.phases.map((p) => p.elapsedMs),
      totalElapsedMs: state.totalElapsedMs,
      totalStartedAt: state.totalStartedAt,
    };
    // 整批重放（模拟 after_seq=0 全量回放 / WS 重连）——全部已 seen，不得改变任何计时。
    const replayed = runEventReducer(state, {
      type: "events",
      events: [
        makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z"),
        makeEvent("tool.started", { tool_name: "search_web" }, 2, "2026-08-03T00:00:40Z"),
        makeEvent("run.succeeded", { report_artifact_id: "a1" }, 3, "2026-08-03T00:01:40Z"),
      ],
    });
    expect(replayed.events).toHaveLength(3);
    expect(replayed.phases.map((p) => p.startedAt)).toEqual(snapshot.startedAt);
    expect(replayed.phases.map((p) => p.status)).toEqual(snapshot.statuses);
    expect(replayed.phases.map((p) => p.elapsedMs)).toEqual(snapshot.elapsedMs);
    expect(replayed.totalElapsedMs).toBe(snapshot.totalElapsedMs);
    expect(replayed.totalStartedAt).toBe(snapshot.totalStartedAt);
  });
});

describe("terminal includes run.interrupted (E1 兼容护栏)", () => {
  it("run.interrupted freezes phases and total (defensive)", () => {
    let state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [makeEvent("run.started", {}, 1, "2026-08-03T00:00:10Z")],
    });
    state = runEventReducer(state, {
      type: "events",
      events: [makeEvent("run.interrupted", { reason: "stale_reclaimed" }, 2, "2026-08-03T00:00:40Z")],
    });
    expect(state.runFinished).toBe(true);
    expect(state.phases[0].status).toBe("done"); // 已 active 的规划阶段被冻结
    expect(state.phases[1].status).toBe("pending"); // 未发生阶段保持 pending
    expect(state.totalElapsedMs).toBe(T40 - T10);
  });
});
