export const OUTPUT_MODES = ["short", "medium", "long"] as const;
export type OutputMode = (typeof OUTPUT_MODES)[number];

export const DEFAULT_OUTPUT_MODE: OutputMode = "medium";

// 保留来源映射：短/中/长 → 3/8/15（后端 ResearchRequest.max_web_sources 范围 ge=3 le=15）。
export const OUTPUT_MODE_MAX_SOURCES: Record<OutputMode, number> = {
  short: 3,
  medium: 8,
  long: 15,
};

export const OUTPUT_MODE_LABELS: Record<OutputMode, string> = {
  short: "短",
  medium: "中",
  long: "长",
};

export function isOutputMode(value: unknown): value is OutputMode {
  return (
    typeof value === "string" && (OUTPUT_MODES as readonly string[]).includes(value)
  );
}

// 旧后端响应缺 output_mode 时默认 medium；非法值同样收敛到 medium。
export function normalizeOutputMode(value: unknown): OutputMode {
  return isOutputMode(value) ? value : DEFAULT_OUTPUT_MODE;
}
