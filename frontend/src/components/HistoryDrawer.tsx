import { useEffect, useRef, useState } from "react";
import { listRunsForSession, listSessions } from "../api/client";
import type { Run, SessionListItem } from "../domain/schemas";

type Props = {
  open: boolean;
  onClose: () => void;
  onRestore: (payload: { sessionId: string; run: Run }) => void;
};

export function HistoryDrawer({ open, onClose, onRestore }: Props) {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const [sessions, setSessions] = useState<SessionListItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [runsBySession, setRunsBySession] = useState<Record<string, Run[]>>({});
  const [runsLoading, setRunsLoading] = useState(false);

  // 打开时加载会话列表，并聚焦抽屉
  useEffect(() => {
    if (!open) {
      return;
    }
    setLoadError(null);
    setSessions(null);
    setExpandedId(null);
    setRunsBySession({});
    closeRef.current?.focus();
    let cancelled = false;
    void listSessions()
      .then((list) => {
        if (!cancelled) setSessions(list);
      })
      .catch((err) => {
        if (!cancelled)
          setLoadError(err instanceof Error ? err.message : "加载历史记录失败");
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  // 展开某会话时加载其 runs
  useEffect(() => {
    if (!open || !expandedId) {
      return;
    }
    let cancelled = false;
    setRunsLoading(true);
    void listRunsForSession(expandedId)
      .then((runs) => {
        if (!cancelled) {
          setRunsBySession((prev) => ({ ...prev, [expandedId]: runs }));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRunsBySession((prev) => ({ ...prev, [expandedId]: [] }));
        }
      })
      .finally(() => {
        if (!cancelled) setRunsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, expandedId]);

  // Esc 关闭
  useEffect(() => {
    if (!open) {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  return (
    <>
      <div
        className={`drawer-overlay ${open ? "open" : ""}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        className={`history-drawer ${open ? "open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label="历史记录"
      >
        <div className="drawer-head">
          <h2>历史记录</h2>
          <button
            ref={closeRef}
            type="button"
            className="drawer-close"
            onClick={onClose}
            aria-label="关闭历史记录"
          >
            ✕
          </button>
        </div>

        <div className="drawer-body">
          {loadError ? <p className="error-text">{loadError}</p> : null}
          {!sessions && !loadError ? (
            <p className="drawer-empty">加载中…</p>
          ) : null}
          {sessions && sessions.length === 0 ? (
            <p className="drawer-empty">暂无历史会话。</p>
          ) : null}
          {sessions?.map((session) => {
            const runs = runsBySession[session.session_id];
            const expanded = expandedId === session.session_id;
            return (
              <div className="drawer-session" key={session.session_id}>
                <div className="drawer-session-head">
                  <span className="drawer-session-name">
                    {session.display_name || session.session_id}
                  </span>
                  <span className="drawer-session-time mono">
                    {new Date(session.created_at).toLocaleDateString()}
                  </span>
                </div>
                <div className="drawer-run-list">
                  {expanded && runsLoading ? (
                    <p className="drawer-status">加载中…</p>
                  ) : null}
                  {(runs ?? []).map((run) => (
                    <button
                      key={run.run_id}
                      type="button"
                      className="drawer-run"
                      onClick={() => onRestore({ sessionId: session.session_id, run })}
                    >
                      <div className="drawer-run-question">{run.question}</div>
                      <div className="drawer-run-meta">
                        <span className="mono">{run.status}</span>
                        <span>{new Date(run.created_at).toLocaleString()}</span>
                      </div>
                    </button>
                  ))}
                  {!expanded && runs === undefined ? (
                    <button
                      type="button"
                      className="drawer-run"
                      onClick={() => setExpandedId(session.session_id)}
                      aria-expanded={false}
                    >
                      <div className="drawer-run-meta">
                        <span>查看该会话的运行记录</span>
                        <span aria-hidden="true">▸</span>
                      </div>
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}
