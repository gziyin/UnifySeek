import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TimelineCard } from "../src/components/TimelineCard";
import {
  initialRunViewState,
  runEventReducer,
  type TerminalRunStatus,
} from "../src/state/runEventReducer";

const FINISHED_AT = Date.parse("2026-08-21T02:03:12Z");

describe("TimelineCard terminal status", () => {
  it.each<[TerminalRunStatus, string]>([
    ["succeeded", "研究完成"],
    ["failed", "研究失败"],
    ["cancelled", "已取消"],
    ["interrupted", "已中断"],
  ])("connected + %s renders %s instead of researching", (status, label) => {
    const state = runEventReducer(
      { ...initialRunViewState, connection: "connected" },
      { type: "terminalSync", status, at: FINISHED_AT },
    );

    const html = renderToStaticMarkup(createElement(TimelineCard, { state }));

    expect(html).toContain(label);
    expect(html).not.toContain("研究中");
  });
});
