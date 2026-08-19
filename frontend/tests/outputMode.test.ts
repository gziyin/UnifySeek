import { describe, expect, it } from "vitest";
import {
  DEFAULT_OUTPUT_MODE,
  OUTPUT_MODES,
  OUTPUT_MODE_LABELS,
  OUTPUT_MODE_MAX_SOURCES,
  isOutputMode,
  normalizeOutputMode,
  type OutputMode,
} from "../src/domain/outputMode";
import { RunSchema } from "../src/domain/schemas";
import type { Run } from "../src/domain/schemas";

const UUID = "11111111-1111-4111-8111-111111111111";

describe("outputMode domain", () => {
  it("OUTPUT_MODES covers short|medium|long in order", () => {
    expect(OUTPUT_MODES).toEqual(["short", "medium", "long"]);
  });

  it("OUTPUT_MODE_MAX_SOURCES keeps 3/8/15 mapping", () => {
    expect(OUTPUT_MODE_MAX_SOURCES.short).toBe(3);
    expect(OUTPUT_MODE_MAX_SOURCES.medium).toBe(8);
    expect(OUTPUT_MODE_MAX_SOURCES.long).toBe(15);
  });

  it("OUTPUT_MODE_LABELS maps to 短/中/长", () => {
    expect(OUTPUT_MODE_LABELS.short).toBe("短");
    expect(OUTPUT_MODE_LABELS.medium).toBe("中");
    expect(OUTPUT_MODE_LABELS.long).toBe("长");
  });

  it("isOutputMode only accepts the three modes", () => {
    expect(isOutputMode("short")).toBe(true);
    expect(isOutputMode("medium")).toBe(true);
    expect(isOutputMode("long")).toBe(true);
    expect(isOutputMode("xlong")).toBe(false);
    expect(isOutputMode("")).toBe(false);
    expect(isOutputMode(1)).toBe(false);
    expect(isOutputMode(undefined)).toBe(false);
    expect(isOutputMode(null)).toBe(false);
  });

  it("normalizeOutputMode passes through valid modes", () => {
    expect(normalizeOutputMode("short")).toBe("short");
    expect(normalizeOutputMode("medium")).toBe("medium");
    expect(normalizeOutputMode("long")).toBe("long");
  });

  it("normalizeOutputMode defaults unknown/missing values to medium", () => {
    expect(normalizeOutputMode(undefined)).toBe("medium");
    expect(normalizeOutputMode(null)).toBe("medium");
    expect(normalizeOutputMode(0)).toBe("medium");
    expect(normalizeOutputMode("xlong")).toBe("medium");
    expect(normalizeOutputMode("")).toBe("medium");
    expect(DEFAULT_OUTPUT_MODE).toBe("medium");
  });
});

function baseRun(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: UUID,
    session_id: UUID,
    status: "succeeded",
    question: "Test question",
    created_at: "2026-08-18T00:00:00Z",
    last_seq: 5,
    ...overrides,
  };
}

describe("RunSchema output_mode contract", () => {
  it("parses explicit output_mode", () => {
    const run = RunSchema.parse(baseRun({ output_mode: "long" })) as Run;
    expect(run.output_mode).toBe("long");
  });

  it("defaults to medium when output_mode is missing (legacy backend response)", () => {
    const run = RunSchema.parse(baseRun({})) as Run;
    expect(run.output_mode).toBe("medium");
  });

  it("coerces invalid/unknown output_mode to medium", () => {
    expect((RunSchema.parse(baseRun({ output_mode: "xlong" })) as Run).output_mode).toBe(
      "medium",
    );
    expect((RunSchema.parse(baseRun({ output_mode: null })) as Run).output_mode).toBe(
      "medium",
    );
    expect((RunSchema.parse(baseRun({ output_mode: 42 })) as Run).output_mode).toBe(
      "medium",
    );
  });
});