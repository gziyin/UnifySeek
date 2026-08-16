import { useMemo, useState } from "react";
import type { RunViewState } from "../state/runEventReducer";

type Props = {
  sources: RunViewState["sources"];
  citedIds?: string[] | null;
};

export type LedgerSource = RunViewState["sources"][number];

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

export type LedgerPartition = {
  /** 主视图条目：citedIds 非空时为被引用集（按 citedIds 顺序），否则为全量。 */
  main: RunViewState["sources"];
  /** 未引用条目（仅 citedMode 时有意义）。 */
  uncited: RunViewState["sources"];
  /** 是否处于「与报告 Sources 对齐」模式（citedIds 非空）。 */
  citedMode: boolean;
};

/**
 * 账本分区（批次D）：citedIds 非空 → 主视图 = sources 中被引用者、按 citedIds 顺序
 * （与报告 [n] 编号一致），未引用收折叠区；citedIds 为空/未就绪（进行中/fake/降级）
 * → 回退全量显示，行为与现状一致。
 */
export function partitionSourcesByCited(
  sources: RunViewState["sources"],
  citedIds: string[] | undefined | null,
): LedgerPartition {
  const cited = citedIds ?? [];
  if (cited.length === 0) {
    return { main: sources, uncited: [], citedMode: false };
  }
  const byId = new Map<string, RunViewState["sources"][number]>();
  for (const s of sources) byId.set(s.evidence_id, s);
  const citedSet = new Set(cited);
  const main: RunViewState["sources"] = [];
  const seen = new Set<string>();
  for (const id of cited) {
    const item = byId.get(id);
    if (item && !seen.has(id)) {
      seen.add(id);
      main.push(item);
    }
  }
  const uncited = sources.filter((s) => !citedSet.has(s.evidence_id));
  return { main, uncited, citedMode: true };
}

function copyText(text: string) {
  void navigator.clipboard?.writeText(text).catch(() => {});
}

function SourceItem({
  item,
  number,
}: {
  item: LedgerSource;
  number?: number;
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
        {number != null ? (
          <span className="mono" style={{ color: "var(--mist)", fontSize: "0.8rem" }}>
            [{number}]
          </span>
        ) : null}
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

export function Ledger({ sources, citedIds }: Props) {
  const [open, setOpen] = useState(false);
  const [showUncited, setShowUncited] = useState(false);

  const partition = useMemo(
    () => partitionSourcesByCited(sources, citedIds),
    [sources, citedIds],
  );
  const { main, uncited, citedMode } = partition;
  const counts = countSourcesByType(main);

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
            {citedMode
              ? `📚 来源账本（${main.length} 条引用 · S:${counts.S} D:${counts.D} K:${counts.K}）`
              : `📚 来源账本（${sources.length} 条证据 · S:${counts.S} D:${counts.D} K:${counts.K}）`}
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
              {main.length === 0 ? (
                <p style={{ color: "var(--mist)", fontSize: "0.85rem" }}>
                  {citedMode ? "报告未引用任何账本证据。" : "尚无来源。"}
                </p>
              ) : null}
              {main.map((item, index) => (
                <SourceItem
                  item={item}
                  key={`${item.evidence_id}-${item.title}-${index}`}
                  number={citedMode ? index + 1 : undefined}
                />
              ))}
            </div>
            {citedMode && uncited.length > 0 ? (
              <div className="ledger-uncited">
                <button
                  type="button"
                  onClick={() => setShowUncited((v) => !v)}
                  aria-expanded={showUncited}
                  style={{
                    background: "none",
                    border: "none",
                    color: "var(--mist)",
                    cursor: "pointer",
                    padding: "0.5rem 0.1rem",
                    font: "inherit",
                    fontSize: "0.8rem",
                  }}
                >
                  <span>📎 未引用的过程证据 {uncited.length} 条</span>
                  <span className="chevron" aria-hidden="true">
                    {showUncited ? "▾" : "▸"}
                  </span>
                </button>
                {showUncited ? (
                  <div className="ledger-list">
                    {uncited.map((item, index) => (
                      <SourceItem
                        item={item}
                        key={`${item.evidence_id}-${item.title}-${index}`}
                      />
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
