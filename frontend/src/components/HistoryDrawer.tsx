import { useCallback, useEffect, useRef, useState } from "react";
import { deleteSession, listRunsForSession, listSessions } from "../api/client";
import type { Run, SessionListItem } from "../domain/schemas";

function statusClass(status: string): string {
  switch (status) {
    case "succeeded":
      return "success";
    case "failed":
      return "danger";
    case "cancelled":
    case "interrupted":
    case "cancelling":
      return "warn";
    default:
      return "";
  }
}

function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) {
    return "";
  }
  const diff = Date.now() - then;
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) {
    return "刚刚";
  }
  if (diff < hour) {
    return `${Math.floor(diff / minute)} 分钟前`;
  }
  if (diff < day) {
    return `${Math.floor(diff / hour)} 小时前`;
  }
  if (diff < 7 * day) {
    return `${Math.floor(diff / day)} 天前`;
  }
  return new Date(then).toLocaleDateString();
}

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

  // 删除会话成功后主动重拉列表，以服务端为准（#38）。
  const reloadSessions = useCallback(async () => {
    try {
      const list = await listSessions();
      setSessions(list);
    } catch {
      // 刷新失败保留现有列表（乐观删除已生效），不做额外处理。
    }
  }, []);

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
      void reloadSessions();
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
                  <button
                    type="button"
                    className="drawer-session-toggle"
                    onClick={() => setExpandedId(expanded ? null : session.session_id)}
                    aria-expanded={expanded}
                  >
                    <span className="drawer-session-icon" aria-hidden="true">
                      {(session.display_name || session.session_id).charAt(0).toUpperCase()}
                    </span>
                    <span className="drawer-session-main">
                      <span className="drawer-session-name">
                        {session.display_name || session.session_id}
                      </span>
                      <span className="drawer-session-time mono">
                        {formatRelativeTime(session.updated_at)}
                      </span>
                    </span>
                    <span className={`drawer-chevron${expanded ? " open" : ""}`} aria-hidden="true">
                      ▸
                    </span>
                  </button>
                  <button
                    type="button"
                    className="drawer-session-delete"
                    onClick={() => void handleDeleteSession(session)}
                    aria-label="删除该会话"
                    title="删除该会话"
                  >
                    🗑
                  </button>
                </div>
                {expanded ? (
                  <div className="drawer-run-list">
                    {runs === undefined ? (
                      <p className="drawer-status">加载中…</p>
                    ) : runs.length === 0 ? (
                      <p className="drawer-empty">暂无运行记录</p>
                    ) : (
                      runs.map((run) => (
                        <button
                          key={run.run_id}
                          type="button"
                          className="drawer-run"
                          onClick={() => onRestore({ sessionId: session.session_id, run })}
                        >
                          <div className="drawer-run-question">{run.question}</div>
                          <div className="drawer-run-meta">
                            <span className={`status-pill ${statusClass(run.status)}`}>
                              <span className="status-dot" aria-hidden="true" />
                              {run.status}
                            </span>
                            <span className="drawer-run-time">
                              {new Date(run.created_at).toLocaleString()}
                            </span>
                          </div>
                        </button>
                      ))
                    )}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}
