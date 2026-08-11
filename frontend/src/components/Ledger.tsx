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

/** S/D/K 计数：按 source_type 映射（web→S、document→D、knowledge_base→K，与 typeHint 一致）。 */
export function countSourcesByType(
  sources: RunViewState["sources"],
): { S: number; D: number; K: number } {
  return {
    S: sources.filter((s) => s.source_type === "web").length,
    D: sources.filter((s) => s.source_type === "document").length,
    K: sources.filter((s) => s.source_type === "knowledge_base").length,
  };
}

function copyText(text: string) {
  void navigator.clipboard?.writeText(text).catch(() => {});
}

function SourceItem({ item }: { item: RunViewState["sources"][number] }) {
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
          <button type="button" className="copy-btn" onClick={handleCopy}>
            {copied ? "已复制" : "复制"}
          </button>
        ) : null}
      </div>
      <div className="ledger-title">{item.title}</div>
      <div style={{ color: "var(--mist)", fontSize: "0.78rem" }}>
        {typeHint(item.source_type)}
        {item.query ? ` · 查询：${item.query}` : ""}
        {item.line_start != null
          ? ` · 行 ${item.line_start}-${item.line_end ?? item.line_start}`
          : ""}
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
        <div className="ledger-excerpt">{item.excerpt}</div>
      ) : null}
    </div>
  );
}

export function Ledger({ sources }: Props) {
  const [open, setOpen] = useState(false);

  const counts = countSourcesByType(sources);

  return (
    <div className="ledger-section">
      <div className="ledger-inner">
        <button
          type="button"
          className={`ledger-toggle ${open ? "open" : ""}`}
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="ledger-content"
        >
          <span>
            📚 来源账本（{sources.length} 条证据 · S:{counts.S} D:{counts.D} K:
            {counts.K}）
          </span>
          <span className="chevron" aria-hidden="true">
            ▾
          </span>
        </button>
        <div
          id="ledger-content"
          className={`ledger-content ${open ? "open" : ""}`}
        >
          <div className="ledger-content-inner">
            <div className="ledger-list">
              {sources.length === 0 ? (
                <p style={{ color: "var(--mist)", fontSize: "0.85rem" }}>尚无来源。</p>
              ) : null}
              {sources.map((item, index) => (
                <SourceItem
                  item={item}
                  key={`${item.evidence_id}-${item.title}-${index}`}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
