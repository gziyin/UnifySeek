import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { artifactDownloadUrl } from "../api/client";

type Props = {
  markdown?: string;
  artifactId?: string;
};

export function ReportViewer({ markdown, artifactId }: Props) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    if (!markdown) return;
    void navigator.clipboard?.writeText(markdown).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };
  return (
    <section className="panel">
      <div className="actions" style={{ justifyContent: "space-between" }}>
        <h2>研究报告</h2>
        <div className="actions">
          {markdown ? (
            <button type="button" className="btn secondary" onClick={handleCopy}>
              {copied ? "已复制" : "复制 Markdown"}
            </button>
          ) : null}
          {artifactId ? (
            <a className="btn secondary" href={artifactDownloadUrl(artifactId)}>
              下载 Markdown
            </a>
          ) : null}
        </div>
      </div>
      <div className="report report-body">
        {markdown ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
            {markdown}
          </ReactMarkdown>
        ) : (
          <p className="muted">报告生成后将显示在这里。</p>
        )}
      </div>
    </section>
  );
}
