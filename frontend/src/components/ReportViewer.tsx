import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { artifactDownloadUrl } from "../api/client";

type Props = {
  markdown?: string;
  artifactId?: string;
  degraded?: boolean;
  reason?: string;
};

export function ReportViewer({ markdown, artifactId, degraded = false, reason }: Props) {
  const [copied, setCopied] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [reasonOpen, setReasonOpen] = useState(false);
  const dialogRef = useRef<HTMLDialogElement | null>(null);

  const handleCopy = () => {
    if (!markdown) return;
    void navigator.clipboard?.writeText(markdown).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  const openPreview = () => {
    setPreviewOpen(true);
  };

  const closePreview = () => {
    setPreviewOpen(false);
  };

  // 用 <dialog> 承载预览：打开时 showModal，关闭时 close。
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (previewOpen && !dialog.open) {
      dialog.showModal();
    } else if (!previewOpen && dialog.open) {
      dialog.close();
    }
  }, [previewOpen]);

  return (
    <section className="panel">
      <div className="actions" style={{ justifyContent: "space-between" }}>
        <h2>研究报告</h2>
        <div className="actions">
          {markdown ? (
            <button type="button" className="btn secondary" onClick={openPreview}>
              预览
            </button>
          ) : null}
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

      {degraded ? (
        <div className="report-banner" role="note">
          <p>本次报告生成未完全达到质量标准，已为你保存可查看版本，可点预览查看详情。</p>
          {reason ? (
            <button
              type="button"
              className="btn secondary report-reason-toggle"
              onClick={() => setReasonOpen((v) => !v)}
              aria-expanded={reasonOpen}
            >
              {reasonOpen ? "收起失败原因" : "展开失败原因"}
            </button>
          ) : null}
          {reasonOpen && reason ? (
            <pre className="report-reason">{reason}</pre>
          ) : null}
        </div>
      ) : null}

      <div className="report report-body">
        {markdown ? (
          <p className="muted">报告已生成，点击「预览」查看 Markdown 渲染结果。</p>
        ) : (
          <p className="muted">报告生成后将显示在这里。</p>
        )}
      </div>

      <dialog
        ref={dialogRef}
        className="report-preview-modal"
        onClose={closePreview}
        onClick={(event) => {
          // 点击遮罩（dialog 自身区域）关闭
          if (event.target === dialogRef.current) {
            closePreview();
          }
        }}
      >
        <div className="report-preview-head">
          <h2>研究报告预览</h2>
          <button
            type="button"
            className="btn secondary"
            onClick={closePreview}
            aria-label="关闭预览"
          >
            关闭
          </button>
        </div>
        <div className="report-preview-body">
          {markdown ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
              {markdown}
            </ReactMarkdown>
          ) : (
            <p className="muted">暂无内容。</p>
          )}
        </div>
      </dialog>
    </section>
  );
}
