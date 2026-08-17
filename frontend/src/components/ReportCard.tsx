import { useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { artifactDownloadUrl } from "../api/client";
import type {
  ResearchClaim,
  ResearchReport,
} from "../domain/schemas";
type Props = {
  markdown?: string;
  artifactId?: string;
  degraded?: boolean;
  reason?: string;
  reportJson?: ResearchReport | null;
};

function ConfidenceBadge({ confidence }: { confidence: string }) {
  const label =
    confidence === "high" ? "高" : confidence === "medium" ? "中" : "低";
  return (
    <span className={`conf-badge ${confidence}`} aria-label={`置信度 ${label}`}>
      {label}
    </span>
  );
}

function ClaimRow({ claim }: { claim: ResearchClaim }) {
  return (
    <div className="rj-claim">
      <ConfidenceBadge confidence={claim.confidence} />
      <div>
        <div>{claim.statement}</div>
        <div className="rj-cites mono">
          [{claim.citation_ids.join(" · ")}]
        </div>
      </div>
    </div>
  );
}

function StructuredReport({ report }: { report: ResearchReport }) {
  const claimById = new Map<string, ResearchClaim>();
  for (const section of report.sections) {
    for (const claim of section.claims) {
      claimById.set(claim.id, claim);
    }
  }
  for (const claim of report.recommendations ?? []) {
    claimById.set(claim.id, claim);
  }

  const executive: ResearchClaim[] = (report.summary_claims ?? []).length
    ? report.summary_claims ?? []
    : (report.executive_summary_claim_ids ?? [])
        .map((id) => claimById.get(id))
        .filter((c): c is ResearchClaim => Boolean(c));

  return (
    <div className="report-json">
      <div className="rj-executive">
        <h3>核心结论</h3>
        {executive.map((claim) => (
          <ClaimRow claim={claim} key={claim.id} />
        ))}
      </div>

      {report.sections.map((section, i) => (
        <div className="rj-section" key={`${section.heading}-${i}`}>
          <h3>{section.heading}</h3>
          {section.claims.map((claim) => (
            <ClaimRow claim={claim} key={claim.id} />
          ))}
        </div>
      ))}

      {(report.disagreements ?? []).map((dis, i) => (
        <div className="rj-disagreement" key={`${dis.topic}-${i}`}>
          <h4>争议点 · {dis.topic}</h4>
          {dis.sides.map((side, j) => (
            <div key={j} style={{ marginBottom: "0.2rem" }}>
              {side.position}{" "}
              <span className="rj-cites mono">[{side.citation_ids.join(" · ")}]</span>
            </div>
          ))}
        </div>
      ))}

      {(report.unknowns ?? []).length ? (
        <div className="rj-unknown">
          <h4>未知 / 待确认</h4>
          <ul style={{ paddingLeft: "1.1rem" }}>
            {report.unknowns.map((u, i) => (
              <li key={i}>{u}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {(report.recommendations ?? []).length ? (
        <div className="rj-section">
          <h3>建议</h3>
          {report.recommendations.map((claim) => (
            <ClaimRow claim={claim} key={claim.id} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function ReportCard({
  markdown,
  artifactId,
  degraded = false,
  reason,
  reportJson,
}: Props) {
  const [copied, setCopied] = useState(false);
  const [view, setView] = useState<"markdown" | "structured">("markdown");
  const hasJson = Boolean(reportJson);

  const handleCopy = () => {
    if (!markdown) return;
    void navigator.clipboard?.writeText(markdown).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  // 章节末尾灰斜体来源行：*来源：[n][n]...* → 加 report-source 类
  const markdownComponents = {
    em: ({ children }: { children?: ReactNode }) => {
      const text = Array.isArray(children) ? children.join("") : String(children ?? "");
      if (text.startsWith("来源：")) {
        return <em className="report-source">{children}</em>;
      }
      return <em>{children}</em>;
    },
  };

  return (
    <section className="glass-card report-card">
      <div className="report-expand">
        <div className="report-expand-inner">
          <div className="report-header">
            <h2>研究报告</h2>
            <div className="report-actions">
              {hasJson ? (
                <button
                  type="button"
                  onClick={() =>
                    setView((v) => (v === "markdown" ? "structured" : "markdown"))
                  }
                >
                  {view === "markdown" ? "结构化" : "Markdown"}
                </button>
              ) : null}
              {markdown ? (
                <button type="button" onClick={handleCopy}>
                  {copied ? "已复制" : "复制"}
                </button>
              ) : null}
              {artifactId ? (
                <a href={artifactDownloadUrl(artifactId)}>下载</a>
              ) : null}
            </div>
          </div>

          {degraded ? (
            <div className="report-banner" role="note">
              <p>本次报告生成未完全达到质量标准，已为你保存可查看版本。</p>
              {reason ? (
                <details className="report-reason-toggle">
                  <summary>展开失败原因</summary>
                  <pre className="report-reason">{reason}</pre>
                </details>
              ) : null}
            </div>
          ) : null}

          <div className="report-preview">
            {hasJson && view === "structured" && reportJson ? (
              <StructuredReport report={reportJson} />
            ) : markdown ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={markdownComponents}>
                {markdown}
              </ReactMarkdown>
            ) : (
              <p style={{ color: "var(--mist)" }}>报告生成后将显示在这里。</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
