import type { Run } from "../domain/schemas";

export type StageVisibilityInput = {
  run: Run | null;
  reportArtifactId: string | undefined;
  phasesAdvanced: boolean;
};

export type StageVisibility = {
  active: boolean;
  reportReady: boolean;
  timelineVisible: boolean;
  showExamples: boolean;
  stageClass: string;
};

/**
 * 起始页阶段化显隐（#47）的纯推导函数：由 run 状态 / 报告就绪 / 阶段推进
 * 推导各卡片是否展示与根容器 stage class。仅消费现有字段，不新增 reducer 状态。
 */
export function deriveStageVisibility({
  run,
  reportArtifactId,
  phasesAdvanced,
}: StageVisibilityInput): StageVisibility {
  const active =
    run?.status === "pending" ||
    run?.status === "running" ||
    run?.status === "cancelling";
  const reportReady = Boolean(reportArtifactId);
  const timelineVisible = active || phasesAdvanced;
  const showExamples = !run && !reportReady;
  const stageClass =
    (timelineVisible ? " stage-running" : "") + (reportReady ? " stage-report" : "");
  return { active, reportReady, timelineVisible, showExamples, stageClass };
}
