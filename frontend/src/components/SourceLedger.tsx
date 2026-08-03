import { useState } from "react";
import type { RunViewState } from "../state/runEventReducer";

type Props = {
  sources: RunViewState["sources"];
};

function typeHint(source_type: string): string {
  switch (source_type) {
    case "web":
      return "S = 网页来源";
    case "document":
      return "D = 上传文档";
    case "knowledge_base":
      return "K = 本地知识库";
    default:
      return source_type;
  }
}

function copyText(text: string) {
  void navigator.clipboard?.writeText(text).catch(() => {});
}

function SourceItem({
  item,
}: {
  item: RunViewState["sources"][number];
}) {
  const [copied, setCopied] = useState(false);
  const target = item.url ?? item.path ?? item.locator ?? "";
  const handleCopy = () => {
    if (!target) return;
    copyText(target);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };
  return (
    <div className="ledger-item">
      <div className="ledger-head">
        <strong className="mono">{item.evidence_id}</strong>
        <span className={`level-badge level-${item.evidence_level}`}>
          {item.evidence_level}
        </span>
        {target ? (
          <button type="button" className="btn copy-btn" onClick={handleCopy}>
            {copied ? "已复制" : "复制"}
          </button>
        ) : null}
      </div>
      <div className="ledger-title">{item.title}</div>
      <div className="muted">
        {typeHint(item.source_type)}
        {item.query ? ` · 查询：${item.query}` : ""}
        {item.line_start != null ? ` · 行 ${item.line_start}-${item.line_end ?? item.line_start}` : ""}
        {item.page != null ? ` · 页 ${item.page}` : ""}
      </div>
      {target ? (
        <div className="ledger-target mono" title={target}>
          {item.url ? (
            <a href={item.url} target="_blank" rel="noreferrer">
              {target}
            </a>
          ) : (
            target
          )}
        </div>
      ) : null}
      {item.excerpt ? (
        <div className="ledger-excerpt muted">{item.excerpt}</div>
      ) : null}
    </div>
  );
}

export function SourceLedger({ sources }: Props) {
  return (
    <section className="panel">
      <h2>来源账本</h2>
      <p className="muted">
        标签含义：S = 网页来源，D = 上传文档，K = 本地知识库。点击「复制」复制完整
        URL / 绝对路径。
      </p>
      <div className="ledger">
        {sources.length === 0 ? <p className="muted">尚无来源。</p> : null}
        {sources.map((item, index) => (
          <SourceItem item={item} key={`${item.evidence_id}-${item.title}-${index}`} />
        ))}
      </div>
    </section>
  );
}
