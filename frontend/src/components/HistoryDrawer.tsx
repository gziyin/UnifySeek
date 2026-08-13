import { useEffect, useRef, useState } from "react";
import { deleteSession, listRunsForSession, listSessions } from "../api/client";
import type { Run, SessionListItem } from "../domain/schemas";

type Props = {
  open: boolean;
  onClose: () => void;
  onRestore: (payload: { sessionId: string; run: Run }) => void;
  /** 删除会话成功后回调（传被删的 session_id），由 Workbench 判断是否为当前激活会话并清空。 */
  onDeleteSession?: (sessionId: string) => void;
  /** 「+ 新建对话」按钮点击回调。 */
  onNewChat?: () => void;
};

export function HistoryDrawer({ open, onClose, onRestore, onDeleteSession, onNewChat }: Props) {
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

  // 右键删除整个会话（#33）：阻止默认菜单 → 确认 → DELETE → 本地移除 + 通知 Workbench。
  const handleDeleteSession = async (session: SessionListItem) => {
    const confirmed = window.confirm(
      `删除该会话及其全部运行记录？\n${session.display_name || session.session_id}`,
    );
    if (!confirmed) {
      return;
    }
    try {
      await deleteSession(session.session_id);
      setSessions((prev) => (prev ?? []).filter((s) => s.session_id !== session.session_id));
      setRunsBySession((prev) => {
        const { [session.session_id]: _removed, ...rest } = prev;
        return rest;
      });
      if (expandedId === session.session_id) {
        setExpandedId(null);
      }
      onDeleteSession?.(session.session_id);
    } catch {
      // 删除失败保留现状；不做额外处理。
    }
  };

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
          <div className="drawer-head-actions">
            {onNewChat ? (
              <button type="button" className="drawer-newchat" onClick={onNewChat}>
                ＋ 新建对话
              </button>
            ) : null}
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
              <div
                className="drawer-session"
                key={session.session_id}
                onContextMenu={(event) => {
                  event.preventDefault();
                  void handleDeleteSession(session);
                }}
                title="右键删除该会话"
              >
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
