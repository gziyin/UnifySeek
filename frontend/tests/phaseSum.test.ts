import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  clampElapsed,
  initialRunViewState,
  runEventReducer,
} from "../src/state/runEventReducer";
import { computeTotalDisplay, TimelineCard } from "../src/components/TimelineCard";
import type { ResearchEvent } from "../src/domain/schemas";

/**
 * 批次 A（#45 计时口径）：总耗时 = 三阶段之和（严格相等）。
 *
 * T1-T5（reducer 级）：
 * - phaseForEvent 修复：tool.started + submit_research_report → report 阶段，
 *   report.startedAt 锚定「开始提交」的 ts（pre-fix 取 report.ready / submit completed 的 ts）。
 * - 望远镜不变量：Σ(phase.elapsedMs) === totalElapsedMs（防回归护栏）。
 * - 写作静默期（最后一条 research 事件 → submit 工具 started）固有落在 research 尾部，
 *   修复不改变其归属（见计划 §2.2）。
 *
 * T6（显示层）：computeTotalDisplay 把总耗时构造为三阶段显示值之和，
 * 在任何 clockOffsetMs 突变 / 各 key 独立棘轮下恒等。
 */

const BASE = Date.parse("2026-08-03T00:00:00Z");

function ev(
  type: string,
  payload: Record<string, unknown>,
  seq: number,
  offsetSec: number,
): ResearchEvent {
  return {
    protocol_version: "1.0",
    event_id: `evt-${seq}`,
    seq,
    session_id: "s1",
    run_id: "r1",
    type,
    occurred_at: new Date(BASE + offsetSec * 1000).toISOString(),
    actor: "system",
    payload,
  };
}

/** 三阶段 elapsedMs 之和（防回归护栏表达式）。 */
function sumPhases(state: { phases: Array<{ elapsedMs: number }> }): number {
  return state.phases.reduce((acc, p) => acc + p.elapsedMs, 0);
}

describe("T1 真实模式全链路（submit tool.started → report 阶段起点）", () => {
  it("report.startedAt 锚定 submit 工具 started，Σ === totalElapsedMs", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        ev("run.started", {}, 1, 0),
        ev("plan.updated", { items: [] }, 2, 1),
        ev("tool.started", { tool_name: "search_web" }, 3, 10),
        ev("tool.completed", { tool_name: "search_web" }, 4, 12),
        ev("source.discovered", { evidence_id: "S1" }, 5, 12),
        ev("tool.started", { tool_name: "submit_research_report" }, 6, 50),
        ev(
          "tool.completed",
          { tool_name: "submit_research_report", artifact_id: "a1" },
          7,
          51,
        ),
        ev("report.ready", { artifact_id: "a1", degraded: false }, 8, 51),
        ev("run.succeeded", { report_artifact_id: "a1" }, 9, 60),
      ],
    });
    expect(state.phases.map((p) => p.status)).toEqual(["done", "done", "done"]);
    expect(state.phases[0].elapsedMs).toBe(10_000); // plan 0→10s
    expect(state.phases[1].elapsedMs).toBe(40_000); // research 10→50s（含写作静默期尾部）
    expect(state.phases[1].startedAt).toBe(BASE + 10_000);
    // 判别断言：report 起点 = submit 工具 started 的 ts（pre-fix 为 report.ready 的 51s）
    expect(state.phases[2].startedAt).toBe(BASE + 50_000);
    expect(state.phases[2].elapsedMs).toBe(10_000); // report 50→60s
    expect(state.totalElapsedMs).toBe(60_000);
    expect(sumPhases(state)).toBe(state.totalElapsedMs);
    expect(state.runFinished).toBe(true);
  });
});

describe("T2 fake 模式链路（无 submit 事件，直接 report.ready）", () => {
  it("report.startedAt = report.ready ts，Σ === totalElapsedMs", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        ev("run.started", {}, 1, 0),
        ev("tool.started", { tool_name: "search_web" }, 2, 10),
        ev("tool.completed", { tool_name: "search_web" }, 3, 12),
        ev("report.ready", { artifact_id: "a1", degraded: false }, 4, 50),
        ev("run.succeeded", { report_artifact_id: "a1" }, 5, 60),
      ],
    });
    expect(state.phases.map((p) => p.status)).toEqual(["done", "done", "done"]);
    expect(state.phases[1].elapsedMs).toBe(40_000); // research 10→50s
    expect(state.phases[2].startedAt).toBe(BASE + 50_000); // = report.ready ts
    expect(state.phases[2].elapsedMs).toBe(10_000);
    expect(state.totalElapsedMs).toBe(60_000);
    expect(sumPhases(state)).toBe(state.totalElapsedMs);
  });
});

describe("T3 submit 开始后又夹杂 research 事件（阶段只前进不回退）", () => {
  it("report.startedAt 不被覆盖，Σ === totalElapsedMs", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        ev("run.started", {}, 1, 0),
        ev("tool.started", { tool_name: "search_web" }, 2, 10),
        ev("tool.completed", { tool_name: "search_web" }, 3, 12),
        ev("source.discovered", { evidence_id: "S1" }, 4, 12),
        ev("tool.started", { tool_name: "submit_research_report" }, 5, 50),
        ev("source.discovered", { evidence_id: "S2" }, 6, 55), // 异常 research 事件
        ev(
          "tool.completed",
          { tool_name: "submit_research_report", artifact_id: "a1" },
          7,
          60,
        ),
        ev("report.ready", { artifact_id: "a1", degraded: false }, 8, 60),
        ev("run.succeeded", { report_artifact_id: "a1" }, 9, 65),
      ],
    });
    expect(state.phases[1].status).toBe("done"); // research 保持 done
    expect(state.phases[2].status).toBe("done");
    expect(state.phases[2].startedAt).toBe(BASE + 50_000); // 不被 55s research 覆盖
    expect(state.phases[2].elapsedMs).toBe(15_000); // report 50→65s
    expect(state.totalElapsedMs).toBe(65_000);
    expect(sumPhases(state)).toBe(state.totalElapsedMs);
  });
});

describe("T4 历史回放（一次 events dispatch 灌入全量）", () => {
  it("Σ === totalElapsedMs，report.startedAt = submit 工具 started ts", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        ev("run.started", {}, 1, 0),
        ev("plan.updated", { items: [] }, 2, 1),
        ev("tool.started", { tool_name: "search_web" }, 3, 10),
        ev("tool.completed", { tool_name: "search_web" }, 4, 12),
        ev("tool.started", { tool_name: "submit_research_report" }, 5, 50),
        ev("report.ready", { artifact_id: "a1", degraded: false }, 6, 51),
        ev(
          "tool.completed",
          { tool_name: "submit_research_report", artifact_id: "a1" },
          7,
          51,
        ),
        ev("run.succeeded", { report_artifact_id: "a1" }, 8, 60),
      ],
    });
    expect(state.phases[2].startedAt).toBe(BASE + 50_000);
    expect(state.totalElapsedMs).toBe(60_000);
    expect(sumPhases(state)).toBe(state.totalElapsedMs);
  });
});

describe("T5 失败终态（run.failed）", () => {
  it("Σ === totalElapsedMs，report.startedAt = submit 工具 started ts", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        ev("run.started", {}, 1, 0),
        ev("tool.started", { tool_name: "search_web" }, 2, 10),
        ev("tool.completed", { tool_name: "search_web" }, 3, 12),
        ev("tool.started", { tool_name: "submit_research_report" }, 4, 50),
        ev(
          "tool.completed",
          { tool_name: "submit_research_report", artifact_id: "a1" },
          5,
          51,
        ),
        ev("run.failed", { reason: "boom" }, 6, 60),
      ],
    });
    expect(state.runFinished).toBe(true);
    expect(state.phases[2].startedAt).toBe(BASE + 50_000);
    expect(state.phases[2].elapsedMs).toBe(10_000);
    expect(state.totalElapsedMs).toBe(60_000);
    expect(sumPhases(state)).toBe(state.totalElapsedMs);
  });
});

describe("T6 显示层：总耗时显示值 = 三阶段显示值之和（构造性恒等）", () => {
  it("computeTotalDisplay 为各阶段显示值之和", () => {
    expect(computeTotalDisplay([10_000, 20_000, 5_000])).toBe(35_000);
    expect(computeTotalDisplay([0, 0, 0])).toBe(0);
    expect(computeTotalDisplay([])).toBe(0);
  });

  it("offset 突变模拟序列：Σ(阶段显示值) 恒等（各 key 独立棘轮不破坏和）", () => {
    // 模拟活跃 run 的显示：plan 冻结、research 先 active 后冻结（report.ready）、report active。
    // now = Date.now() - clockOffsetMs；now 序列含 >150ms 的前后向突变（后台节流恢复/时钟漂移）。
    const planFrozen = 10_000; // 0→10s
    const researchStart = 10_000;
    const reportStart = 50_000;
    const researchFrozen = 40_000; // research 10→50s 冻结值
    const nowSeq = [
      45_000, 46_000, 47_000, 41_000, 42_000, 55_000, 56_000, 51_000, 60_000,
    ];

    let planD = clampElapsed(undefined, planFrozen);
    let researchD = 0;
    let reportD = 0;
    for (const now of nowSeq) {
      planD = clampElapsed(planD, planFrozen);
      const researchRaw = now < reportStart ? now - researchStart : researchFrozen;
      researchD = clampElapsed(researchD, researchRaw);
      const reportRaw = now >= reportStart ? now - reportStart : 0;
      reportD = clampElapsed(reportD, reportRaw);
      const sum = planD + researchD + reportD;
      // 构造性恒等：总耗时 === Σ(阶段显示值)，任何 offset 突变/棘轮下不可破坏
      expect(computeTotalDisplay([planD, researchD, reportD])).toBe(sum);
      expect(computeTotalDisplay([planD, researchD, reportD])).toBeGreaterThanOrEqual(0);
    }
  });

  it("终态无棘轮污染：显示总耗时 === reducer 冻结的 totalElapsedMs", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "events",
      events: [
        ev("run.started", {}, 1, 0),
        ev("tool.started", { tool_name: "search_web" }, 2, 10),
        ev("tool.completed", { tool_name: "search_web" }, 3, 12),
        ev("tool.started", { tool_name: "submit_research_report" }, 4, 50),
        ev("report.ready", { artifact_id: "a1", degraded: false }, 5, 51),
        ev("run.succeeded", { report_artifact_id: "a1" }, 6, 60),
      ],
    });
    expect(state.runFinished).toBe(true);
    const frozenDisplays = state.phases.map((p) =>
      p.status === "done" ? p.elapsedMs : 0,
    );
    expect(computeTotalDisplay(frozenDisplays)).toBe(state.totalElapsedMs);
  });

  it("先按阶段取整秒再求和，避免毫秒余数跨阶段合并进位", () => {
    expect(computeTotalDisplay([1_999, 1_999, 0])).toBe(2_000);
  });

  it.each(["run.succeeded", "run.failed", "run.cancelled", "run.interrupted"])(
    "%s 显示总耗时，运行中不显示",
    (terminalType) => {
      const running = runEventReducer(initialRunViewState, {
        type: "events",
        events: [
          ev("run.started", {}, 1, 0),
          ev("tool.started", { tool_name: "search_web" }, 2, 10),
        ],
      });
      const runningHtml = renderToStaticMarkup(createElement(TimelineCard, { state: running }));
      expect(runningHtml).not.toContain("timeline-total");

      const terminal = runEventReducer(running, {
        type: "events",
        events: [ev(terminalType, {}, 3, 20)],
      });
      const terminalHtml = renderToStaticMarkup(createElement(TimelineCard, { state: terminal }));
      expect(terminalHtml).toContain("timeline-total");
    },
  );
});
