import type { RunViewState } from "../state/runEventReducer";

type Props = {
  sources: RunViewState["sources"];
};

export function SourceLedger({ sources }: Props) {
  return (
    <section className="panel">
      <h2>来源账本</h2>
      <div className="ledger">
        {sources.length === 0 ? <p className="muted">尚无来源。</p> : null}
        {sources.map((item) => (
          <div className="ledger-item" key={`${item.evidence_id}-${item.title}`}>
            <strong>{item.evidence_id}</strong>
            <div>{item.title}</div>
            <div className="muted">
              {item.source_type} · {item.evidence_level}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
