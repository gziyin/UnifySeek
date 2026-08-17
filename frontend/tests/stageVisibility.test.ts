import { describe, expect, it } from "vitest";
import type { Run } from "../src/domain/schemas";
import { deriveStageVisibility } from "../src/pages/stageVisibility";

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    run_id: "5f4d4e0f-9a2b-4c6d-9e7f-1a2b3c4d5e6f",
    session_id: "5f4d4e0f-9a2b-4c6d-9e7f-1a2b3c4d5e70",
    status: "pending",
    question: "测试问题",
    created_at: "2026-08-17T00:00:00Z",
    last_seq: 0,
    ...overrides,
  };
}

describe("deriveStageVisibility (批次C #47)", () => {
  it("冷启动：run=null、无报告、阶段未推进 → 仅 Examples、无 stage class", () => {
    const v = deriveStageVisibility({
      run: null,
      reportArtifactId: undefined,
      phasesAdvanced: false,
    });
    expect(v.active).toBe(false);
    expect(v.reportReady).toBe(false);
    expect(v.timelineVisible).toBe(false);
    expect(v.showExamples).toBe(true);
    expect(v.stageClass).toBe("");
  });

  it.each(["pending", "running", "cancelling"] as const)(
    "run=%s（运行态）→ 时间线展开、Examples 隐藏、stage-running",
    (status) => {
      const v = deriveStageVisibility({
        run: makeRun({ status }),
        reportArtifactId: undefined,
        phasesAdvanced: false,
      });
      expect(v.active).toBe(true);
      expect(v.timelineVisible).toBe(true);
      expect(v.showExamples).toBe(false);
      expect(v.stageClass).toBe(" stage-running");
    },
  );

  it("run=succeeded 且阶段已推进（恢复已完成 run 的 hydrate 后）→ 时间线展开", () => {
    const v = deriveStageVisibility({
      run: makeRun({ status: "succeeded" }),
      reportArtifactId: undefined,
      phasesAdvanced: true,
    });
    expect(v.timelineVisible).toBe(true);
    expect(v.showExamples).toBe(false);
    expect(v.stageClass).toBe(" stage-running");
  });

  it("run=succeeded 且阶段未推进（恢复无事件 run，hydrate 前）→ 时间线不展开", () => {
    const v = deriveStageVisibility({
      run: makeRun({ status: "succeeded" }),
      reportArtifactId: undefined,
      phasesAdvanced: false,
    });
    expect(v.timelineVisible).toBe(false);
    expect(v.stageClass).toBe("");
  });

  it("reportReady=true → stage-report 展开、Examples 隐藏", () => {
    const v = deriveStageVisibility({
      run: makeRun({ status: "succeeded" }),
      reportArtifactId: "5f4d4e0f-9a2b-4c6d-9e7f-1a2b3c4d5e71",
      phasesAdvanced: true,
    });
    expect(v.reportReady).toBe(true);
    expect(v.showExamples).toBe(false);
    expect(v.stageClass).toBe(" stage-running stage-report");
  });

  it("run=null 但报告就绪（防御态）→ 报告仍展开、Examples 隐藏", () => {
    const v = deriveStageVisibility({
      run: null,
      reportArtifactId: "5f4d4e0f-9a2b-4c6d-9e7f-1a2b3c4d5e71",
      phasesAdvanced: false,
    });
    expect(v.reportReady).toBe(true);
    expect(v.timelineVisible).toBe(false);
    expect(v.showExamples).toBe(false);
    expect(v.stageClass).toBe(" stage-report");
  });

  it("全组合冒烟：stageClass 不含未满足的 class 片段", () => {
    const statuses: Run["status"][] = [
      "pending",
      "running",
      "cancelling",
      "succeeded",
      "failed",
      "interrupted",
      "cancelled",
    ];
    for (const status of statuses) {
      for (const hasReport of [false, true]) {
        for (const phasesAdvanced of [false, true]) {
          const v = deriveStageVisibility({
            run: makeRun({ status }),
            reportArtifactId: hasReport ? "5f4d4e0f-9a2b-4c6d-9e7f-1a2b3c4d5e71" : undefined,
            phasesAdvanced,
          });
          expect(v.stageClass.includes("stage-running")).toBe(v.timelineVisible);
          expect(v.stageClass.includes("stage-report")).toBe(v.reportReady);
          expect(v.showExamples).toBe(false);
        }
      }
    }
  });

  it("active 与 phasesAdvanced 独立生效：active=true 即使阶段未推进也展开", () => {
    const v = deriveStageVisibility({
      run: makeRun({ status: "running" }),
      reportArtifactId: undefined,
      phasesAdvanced: false,
    });
    expect(v.active).toBe(true);
    expect(v.timelineVisible).toBe(true);
  });
});
