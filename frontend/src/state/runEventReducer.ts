import type { ResearchEvent } from "../domain/schemas";

export type ConnectionState = "idle" | "connecting" | "connected" | "reconnecting" | "offline";

export type PhaseKey = "plan" | "research" | "report";
export type PhaseStatus = "pending" | "active" | "done";

export type Phase = {
  key: PhaseKey;
  label: string;
  status: PhaseStatus;
  /** 已冻结耗时（ms）：status === "done" 时有效；active 时由组件用 startedAt 实时计算。 */
  elapsedMs: number;
  /** 事件时间戳（epoch ms，取自该阶段首事件的 occurred_at）；active 阶段的开始时刻。 */
  startedAt: number;
};

export type RunViewState = {
  events: ResearchEvent[];
  lastSeq: number;
  connection: ConnectionState;
  todos: Array<{ id: string; content: string; status: string }>;
  sources: Array<{
    evidence_id: string;
    source_type: string;
    title: string;
    evidence_level: string;
    url?: string;
    path?: string;
    locator?: string;
    query?: string;
    line_start?: number | null;
    line_end?: number | null;
    page?: number | null;
    excerpt?: string;
  }>;
  reportArtifactId?: string;
  reportDegraded: boolean;
  reportReason?: string;
  /** 3 个大阶段：规划 → 调研取证 → 报告生成。 */
  phases: Phase[];
  /** 总计时起点（事件时间戳 epoch ms，取自 run.started 事件）；null 表示尚未开始。 */
  totalStartedAt: number | null;
  /** 总耗时冻结值（ms）：runFinished 后有效。 */
  totalElapsedMs: number;
  /** run 是否已达终态（succeeded/failed/cancelled）：终态后计时冻结。 */
  runFinished: boolean;
};

const PHASE_DEFS: Array<{ key: PhaseKey; label: string }> = [
  { key: "plan", label: "规划阶段" },
  { key: "research", label: "调研取证" },
  { key: "report", label: "报告生成" },
];

export function createInitialPhases(): Phase[] {
  return PHASE_DEFS.map((p) => ({
    key: p.key,
    label: p.label,
    status: "pending",
    elapsedMs: 0,
    startedAt: 0,
  }));
}

export const initialRunViewState: RunViewState = {
  events: [],
  lastSeq: 0,
  connection: "idle",
  todos: [],
  sources: [],
  reportDegraded: false,
  phases: createInitialPhases(),
  totalStartedAt: null,
  totalElapsedMs: 0,
  runFinished: false,
};

export type RunViewAction =
  | { type: "reset" }
  | { type: "connection"; connection: ConnectionState }
  | { type: "events"; events: ResearchEvent[] };

const TERMINAL_TYPES = new Set(["run.succeeded", "run.failed", "run.cancelled"]);

/**
 * 事件 → 时钟起点（epoch ms）。
 *
 * 计时以事件自身的 `occurred_at` 为准，而非共享的 `performance.now()`：hydrate 时
 * `listEvents` 会把一整批事件在一次 dispatch 中灌入，若共用同一 `now`，前一阶段
 * elapsed 会被后一阶段清零（#34）。解析失败兜底为 0（调用侧有 Math.max(0, ...) 保护）。
 */
const evTs = (event: ResearchEvent): number => Date.parse(event.occurred_at) || 0;

/** 事件 → 大阶段映射（依据 Agent 内部执行流程）。submit_research_report 归入报告阶段。 */
function phaseForEvent(event: ResearchEvent): PhaseKey | null {
  const type = event.type;
  if (type === "run.started" || type === "plan.updated") return "plan";
  if (type === "report.ready") return "report";
  if (
    type === "tool.completed" &&
    (event.payload as { tool_name?: string }).tool_name === "submit_research_report"
  )
    return "report";
  if (
    type === "agent.started" ||
    type === "agent.completed" ||
    type === "tool.started" ||
    type === "tool.completed" ||
    type === "tool.failed" ||
    type === "source.discovered" ||
    type === "evidence.recorded"
  )
    return "research";
  return null;
}

/** 阶段索引：plan=0, research=1, report=2。 */
const PHASE_INDEX: Record<PhaseKey, number> = { plan: 0, research: 1, report: 2 };

/**
 * 阶段推进（只前推、不回退）：
 * - 首次遇到某阶段事件 → 该阶段 active（记录 startedAt）并结束前一个阶段（冻结 elapsedMs）。
 * - 遇到 run 终态事件 → 所有 active 阶段冻结为 done，并冻结总计时。
 * 返回拷贝后的新 phases 与总计时状态。
 *
 * 时钟基准：`ts` 是「当前事件」的 occurred_at 时间戳（epoch ms，由调用方经 evTs 传入）。
 * 每个事件用各自的时间戳推进，避免 hydrate 批量灌入共用同一 now 导致前一阶段 elapsed 被清零（#34）。
 */
function advancePhases(
  phases: Phase[],
  totalStartedAt: number | null,
  totalElapsedMs: number,
  runFinished: boolean,
  key: PhaseKey | null,
  isTerminal: boolean,
  ts: number,
): { phases: Phase[]; totalStartedAt: number | null; totalElapsedMs: number; runFinished: boolean } {
  if (isTerminal && runFinished) {
    // 终态已冻结过，保持幂等。
    return { phases, totalStartedAt, totalElapsedMs, runFinished };
  }
  const next = phases.map((p) => ({ ...p }));
  let newTotal = totalStartedAt;
  let newElapsed = totalElapsedMs;
  let newFinished = runFinished;

  if (key && !newFinished) {
    if (newTotal == null) newTotal = ts;
    const idx = PHASE_INDEX[key];
    // 结束 idx 之前所有尚未 done 的阶段。
    for (let i = 0; i < idx; i += 1) {
      if (next[i].status === "active") {
        next[i].status = "done";
        next[i].elapsedMs = Math.max(0, ts - next[i].startedAt);
      } else if (next[i].status === "pending") {
        // 跳过前序阶段事件直接进入更后阶段（如直接 report.ready）：标记为跳过/未发生。
        next[i].status = "done";
        next[i].elapsedMs = 0;
      }
    }
    if (next[idx].status === "pending") {
      next[idx].status = "active";
      next[idx].startedAt = ts;
    }
  }

  if (isTerminal) {
    newFinished = true;
    for (const p of next) {
      if (p.status === "active") {
        p.status = "done";
        p.elapsedMs = Math.max(0, ts - p.startedAt);
      }
    }
    if (newTotal != null) {
      newElapsed = Math.max(0, ts - newTotal);
    }
  }

  return { phases: next, totalStartedAt: newTotal, totalElapsedMs: newElapsed, runFinished: newFinished };
}

export function runEventReducer(state: RunViewState, action: RunViewAction): RunViewState {
  switch (action.type) {
    case "reset":
      return { ...initialRunViewState };
    case "connection":
      return { ...state, connection: action.connection };
    case "events": {
      const merged = [...state.events];
      const seen = new Set(merged.map((item) => item.seq));
      for (const event of action.events) {
        if (event.type === "heartbeat" || event.seq < 0) {
          continue;
        }
        if (seen.has(event.seq)) {
          continue;
        }
        merged.push(event);
        seen.add(event.seq);
      }
      merged.sort((a, b) => a.seq - b.seq);
      const lastSeq = merged.reduce((max, item) => Math.max(max, item.seq), state.lastSeq);
      const todos = [...state.todos];
      const sources = [...state.sources];
      let reportArtifactId = state.reportArtifactId;
      let reportDegraded = state.reportDegraded;
      let reportReason = state.reportReason;

      // 阶段推进：以每个事件自身 occurred_at 为时钟起点，只针对「新事件」推进
      // （避免已 seen 重复事件扰动）。不用共享 performance.now()——hydrate 批量灌入时
      // 共用同一 now 会让前一阶段 elapsed 被清零（#34）。
      let { phases, totalStartedAt, totalElapsedMs, runFinished } = state;

      // 处理本批全部有效事件（合并去重在上方循环完成；阶段推进对重复事件幂等）。
      for (const event of action.events) {
        if (event.type === "heartbeat" || event.seq < 0) {
          continue;
        }
        if (event.type === "plan.updated" && Array.isArray(event.payload.items)) {
          todos.splice(0, todos.length, ...(event.payload.items as RunViewState["todos"]));
        }
        if (event.type === "source.discovered") {
          sources.push({
            evidence_id: String(event.payload.evidence_id ?? ""),
            source_type: String(event.payload.source_type ?? ""),
            title: String(event.payload.title ?? ""),
            evidence_level: String(event.payload.evidence_level ?? ""),
            url: event.payload.url != null ? String(event.payload.url) : undefined,
            path: event.payload.path != null ? String(event.payload.path) : undefined,
            locator:
              event.payload.locator != null ? String(event.payload.locator) : undefined,
            query: event.payload.query != null ? String(event.payload.query) : undefined,
            line_start: event.payload.line_start ?? undefined,
            line_end: event.payload.line_end ?? undefined,
            page: event.payload.page ?? undefined,
            excerpt:
              event.payload.excerpt != null ? String(event.payload.excerpt) : undefined,
          });
        }
        // 契约：report.ready 事件 payload 含 artifact_id + degraded（冻结字段名，前端只做消费适配）。
        if (event.type === "report.ready" || event.type === "run.succeeded") {
          reportArtifactId = String(
            event.payload.artifact_id ?? event.payload.report_artifact_id ?? reportArtifactId ?? "",
          );
          if (!reportArtifactId) {
            reportArtifactId = undefined;
          }
          if (event.type === "report.ready") {
            reportDegraded = Boolean(event.payload.degraded);
            if (event.payload.reason != null) {
              reportReason = String(event.payload.reason);
            }
          }
        }
        // 真实后端里 report.ready 的 payload 只有 {artifact_id, degraded}，降级原因在紧随其后的
        // submit_research_report 的 tool.completed 事件 payload.reason 中（stream_adapter 注入），
        // 前端在此补齐 reason，保证「展开失败原因」可用（不改后端契约）。
        if (
          event.type === "tool.completed" &&
          event.payload.tool_name === "submit_research_report" &&
          event.payload.reason != null
        ) {
          reportReason = String(event.payload.reason);
        }

        const ts = evTs(event);
        const advanced = advancePhases(
          phases,
          totalStartedAt,
          totalElapsedMs,
          runFinished,
          phaseForEvent(event),
          TERMINAL_TYPES.has(event.type),
          ts,
        );
        phases = advanced.phases;
        totalStartedAt = advanced.totalStartedAt;
        totalElapsedMs = advanced.totalElapsedMs;
        runFinished = advanced.runFinished;
      }
      return {
        ...state,
        events: merged,
        lastSeq,
        todos,
        sources,
        reportArtifactId,
        reportDegraded,
        reportReason,
        phases,
        totalStartedAt,
        totalElapsedMs,
        runFinished,
      };
    }
    default:
      return state;
  }
}
