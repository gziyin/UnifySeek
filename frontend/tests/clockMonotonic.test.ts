import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CLOCK_SYNC_THRESHOLD_MS,
  clampElapsed,
  initialRunViewState,
  runEventReducer,
} from "../src/state/runEventReducer";

/**
 * 批次A（#42 残余修复）：
 * - clockSync 防抖：阈值 CLOCK_SYNC_THRESHOLD_MS（默认 150ms）吸收网络/DB/WS 队列
 *   延迟抖动，仅响应真实时钟漂移，避免显示时钟 now 来回平移造成「忽快忽慢」。
 * - clampElapsed 单调钳制：显示值不小于上一次渲染值，杜绝 offset 突变回跳与
 *   active→done 切换回跳。
 * 用 fake timers 冻结 Date.now()，保证样本偏移确定性。
 */
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-08-16T00:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("clockSync hysteresis (#42 残余根因1: offset 抖动)", () => {
  it("establishes offset from a first large sample", () => {
    const T = Date.now();
    const state = runEventReducer(initialRunViewState, {
      type: "clockSync",
      serverTimeMs: T - 1000,
    });
    expect(state.clockOffsetMs).toBe(1000);
  });

  it("rejects sub-threshold jitter (offset stays put)", () => {
    const T = Date.now();
    const base = runEventReducer(initialRunViewState, {
      type: "clockSync",
      serverTimeMs: T - 1000,
    });
    // 新样本偏移 900ms，与当前 1000ms 差 100ms < 阈值 → 忽略
    const delta = CLOCK_SYNC_THRESHOLD_MS - 50;
    const state = runEventReducer(base, {
      type: "clockSync",
      serverTimeMs: T - (1000 - delta),
    });
    expect(state.clockOffsetMs).toBe(base.clockOffsetMs);
  });

  it("applies deltas at/above threshold", () => {
    const T = Date.now();
    const base = runEventReducer(initialRunViewState, {
      type: "clockSync",
      serverTimeMs: T - 1000,
    });
    // 新样本偏移 1200ms，差 200ms >= 阈值 → 应用
    const delta = CLOCK_SYNC_THRESHOLD_MS + 50;
    const state = runEventReducer(base, {
      type: "clockSync",
      serverTimeMs: T - (1000 + delta),
    });
    expect(state.clockOffsetMs).toBe(1000 + delta);
  });

  it("non-finite serverTimeMs falls back to 0 without crashing", () => {
    const state = runEventReducer(initialRunViewState, {
      type: "clockSync",
      serverTimeMs: Number.NaN,
    });
    expect(state.clockOffsetMs).toBe(0);
  });
});

describe("clampElapsed monotonic guard (#42 残余根因2: 单调性缺失)", () => {
  it("first render uses computed, floored at 0", () => {
    expect(clampElapsed(undefined, 5000)).toBe(5000);
    expect(clampElapsed(undefined, -300)).toBe(0);
  });

  it("never decreases under backward jitter and re-progresses forward", () => {
    let v = clampElapsed(undefined, 5000);
    v = clampElapsed(v, 4200);
    v = clampElapsed(v, 4900);
    v = clampElapsed(v, 5100);
    expect(v).toBe(5100); // 4200/4900 被吸收，5100 通过
  });

  it("frozen value keeps monotonic display at active→done transition", () => {
    let v = clampElapsed(undefined, 5000);
    expect(clampElapsed(v, 4900)).toBe(5000); // 冻结值 < 已显示 → 保持（不回跳）
    expect(clampElapsed(v, 5200)).toBe(5200); // 冻结值更大 → 采用
  });
});

describe("simulated run display: per-second monotonic (验收: 无回跳/无忽快忽慢)", () => {
  it("displayed elapsed is monotonic under offset jitter and a 2s backward spike", () => {
    const startedAt = 10_000;
    // 模拟 now 序列：正常推进 + ±抖动 + 一次 -2s 突变（offset 校准/突变）
    const nowSeq = [
      11_000, 12_000, 13_000, 13_100, 12_850, 13_500, 14_200, 11_500, 12_900, 14_000,
      15_000,
    ];
    let prev: number | undefined;
    const displayed: number[] = [];
    for (const now of nowSeq) {
      prev = clampElapsed(prev, now - startedAt);
      displayed.push(prev);
    }
    for (let i = 1; i < displayed.length; i += 1) {
      expect(displayed[i]).toBeGreaterThanOrEqual(displayed[i - 1]);
    }
    // 最终收敛到真实末值，不被历史回跳拖住
    expect(displayed[displayed.length - 1]).toBe(nowSeq[nowSeq.length - 1] - startedAt);
  });

  it("clockSync hysteresis keeps displayed clock stable across event-driven calibrations", () => {
    const T = Date.now();
    // 以真实抖动幅度（<阈值）交替校准：offset 不更新 → 显示时钟 now 保持稳定
    let state = runEventReducer(initialRunViewState, {
      type: "clockSync",
      serverTimeMs: T - 1000,
    });
    const startedAt = T - 500;
    let now = Date.now() - state.clockOffsetMs;
    let prev: number | undefined;
    for (let i = 0; i < 5; i += 1) {
      const jitter = (i % 2 === 0 ? 1 : -1) * (CLOCK_SYNC_THRESHOLD_MS - 60);
      state = runEventReducer(state, {
        type: "clockSync",
        serverTimeMs: T - 1000 + jitter,
      });
      expect(state.clockOffsetMs).toBe(1000); // 每次抖动都被防抖吸收
      now = Date.now() - state.clockOffsetMs;
      prev = clampElapsed(prev, now - startedAt);
    }
    // offset 稳定 → 显示流逝仅由 Date.now() 单调推进，且钳制兜底
    expect(prev).toBeGreaterThanOrEqual(0);
  });
});
