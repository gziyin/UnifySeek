import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ClaimRow, ReportCard } from "../src/components/ReportCard";

function renderClaim(statement: string) {
  return renderToStaticMarkup(
    <ClaimRow
      claim={{
        id: "claim-1",
        statement,
        citation_ids: ["K1", "S1"],
        confidence: "high",
      }}
    />,
  );
}

function renderClaimWithEmphasis(statement: string, emphasize: boolean) {
  return renderToStaticMarkup(
    <ClaimRow
      {...({
        claim: {
          id: "claim-1",
          statement,
          citation_ids: ["K1", "S1"],
          confidence: "high",
        },
        emphasize,
      } as Parameters<typeof ClaimRow>[0])}
    />,
  );
}

describe("structured report claim Markdown", () => {
  it("renders bold claim text as strong while preserving citation_ids", () => {
    const html = renderClaim("核心结论是**策略与执行分离**");

    expect(html).toContain("<strong>策略与执行分离</strong>");
    expect(html).toContain("[K1 · S1]");
    expect(html).not.toContain("**策略与执行分离**");
  });

  it("renders English quoted emphasis in claims", () => {
    const html = renderClaim('Use **English "quoted" emphasis** here');

    expect(html).toContain('<strong>English &quot;quoted&quot; emphasis</strong>');
  });

  it("keeps raw HTML out of claim output", () => {
    const html = renderClaim("**安全文本** <script>alert(1)</script>");

    expect(html).toContain("<strong>安全文本</strong>");
    expect(html).not.toContain("<script>");
  });

  it("deterministically emphasizes the first qualified clause of an unformatted core claim", () => {
    const html = renderClaimWithEmphasis("系统设计原则是：先保证证据可追溯。", true);

    expect(html).toContain("<strong>系统设计原则是</strong>：");
  });

  it("cleans incomplete emphasis and keeps searching past a short clause", () => {
    const html = renderClaimWithEmphasis("结论是，系统设计原则**：先保证证据可追溯。", true);

    expect(html).toContain("<strong>结论是，系统设计原则</strong>：");
  });

  it("emphasizes an entire claim when no qualified separator exists", () => {
    const html = renderClaimWithEmphasis("这是一个没有分隔符但足够长的核心结论", true);

    expect(html).toContain("<strong>这是一个没有分隔符但足够长的核心结论</strong>");
  });

  it("formats only the core conclusions markdown section", () => {
    const html = renderToStaticMarkup(
      <ReportCard
        markdown={[
          "## 核心结论",
          "系统设计原则是：先保证证据可追溯。",
          "",
          "*来源：[1]*",
          "",
          "## 建议",
          "系统设计原则是：普通建议不应被自动加粗。",
        ].join("\n")}
      />,
    );

    expect(html).toContain("<strong>系统设计原则是</strong>：先保证证据可追溯。");
    expect(html).toContain("系统设计原则是：普通建议不应被自动加粗。");
    expect(html).toContain("<em class=\"report-source\">来源：[1]</em>");
  });

  it("preserves nested Sources headings and entries inside the core section", () => {
    const html = renderToStaticMarkup(
      <ReportCard
        markdown={[
          "## 核心结论",
          "系统设计原则是：先保证证据可追溯。",
          "",
          "### Sources",
          "- [1] https://example.com/source",
          "",
          "## 建议",
          "保持普通文本。",
        ].join("\n")}
      />,
    );

    expect(html).toContain("<h3>Sources</h3>");
    expect(html).toContain('href="https://example.com/source"');
    expect(html).not.toContain("<strong>Sources</strong>");
    expect(html).not.toContain("<strong>[1] https://example.com/source</strong>");
  });

  it("does not emphasize ordinary structured claims", () => {
    const html = renderClaimWithEmphasis("系统设计原则是：普通章节保持原样。", false);

    expect(html).not.toContain("<strong>");
    expect(html).toContain("系统设计原则是：普通章节保持原样。");
  });
});
