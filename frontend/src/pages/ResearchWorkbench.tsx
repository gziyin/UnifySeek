import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  cancelRun,
  createRun,
  createSession,
  getArtifactContent,
  getRun,
  listEvents,
  uploadFile,
} from "../api/client";
import type { Artifact, Run } from "../domain/schemas";
import { ResearchEventSchema } from "../domain/schemas";
import { AgentTimeline } from "../components/AgentTimeline";
import { ReportViewer } from "../components/ReportViewer";
import { ResearchBriefForm } from "../components/ResearchBriefForm";
import { SourceLedger } from "../components/SourceLedger";
import { UploadPanel } from "../components/UploadPanel";
import { initialRunViewState, runEventReducer } from "../state/runEventReducer";

const SESSION_KEY = "ai_dev_researcher.session_id";

export function ResearchWorkbench() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [reportMarkdown, setReportMarkdown] = useState<string>("");
  const [bootError, setBootError] = useState<string | null>(null);
  const [view, dispatch] = useReducer(runEventReducer, initialRunViewState);
  // lastSeq ??????? ref ???? WebSocket ??????????
  const lastSeqRef = useRef<number>(0);
  useEffect(() => {
    lastSeqRef.current = view.lastSeq;
  }, [view.lastSeq]);

  useEffect(() => {
    let cancelled = false;
    async function boot() {
      try {
        const existing = localStorage.getItem(SESSION_KEY);
        if (existing) {
          if (!cancelled) {
            setSessionId(existing);
          }
          return;
        }
        const session = await createSession();
        localStorage.setItem(SESSION_KEY, session.session_id);
        if (!cancelled) {
          setSessionId(session.session_id);
        }
      } catch (err) {
        if (!cancelled) {
          setBootError(err instanceof Error ? err.message : "无法创建会话");
        }
      }
    }
    void boot();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!run) {
      return;
    }
    let disposed = false;
    let socket: WebSocket | null = null;
    let retry = 0;
    let timer: number | undefined;

    const runId = run.run_id;

    async function hydrate() {
      const events = await listEvents(runId, 0);
      if (!disposed) {
        dispatch({ type: "events", events });
      }
    }

    function connect() {
      dispatch({ type: "connection", connection: retry === 0 ? "connecting" : "reconnecting" });
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const afterSeq = lastSeqRef.current;
      socket = new WebSocket(
        `${protocol}://${window.location.host}/ws/runs/${runId}?after_seq=${afterSeq}`,
      );
      socket.onopen = () => {
        retry = 0;
        dispatch({ type: "connection", connection: "connected" });
      };
      socket.onmessage = (message) => {
        try {
          const parsed = ResearchEventSchema.safeParse(JSON.parse(String(message.data)));
          if (!parsed.success) {
            return;
          }
          dispatch({ type: "events", events: [parsed.data] });
        } catch {
          // ignore malformed frames
        }
      };
      socket.onclose = () => {
        if (disposed) {
          return;
        }
        dispatch({ type: "connection", connection: "reconnecting" });
        const delay = Math.min(8000, 500 * 2 ** retry) + Math.floor(Math.random() * 300);
        retry += 1;
        timer = window.setTimeout(connect, delay);
      };
    }

    void hydrate().then(connect);
    const poll = window.setInterval(() => {
      void getRun(runId).then((fresh) => {
        if (disposed) {
          return;
        }
        setRun(fresh);
        // run 到达终态后停止轮询（WS 仍保持连接，承担后续兜底）
        if (
          fresh.status === "succeeded" ||
          fresh.status === "failed" ||
          fresh.status === "interrupted" ||
          fresh.status === "cancelled"
        ) {
          window.clearInterval(poll);
        }
      });
    }, 1500);

    return () => {
      disposed = true;
      window.clearInterval(poll);
      if (timer) {
        window.clearTimeout(timer);
      }
      socket?.close();
    };
    // intentionally depend on run id only; lastSeq is read at connect time via closure refresh on remount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.run_id]);

  useEffect(() => {
    const artifactId = view.reportArtifactId ?? run?.report_artifact_id ?? undefined;
    if (!artifactId) {
      return;
    }
    let cancelled = false;
    void getArtifactContent(artifactId).then((content) => {
      if (!cancelled) {
        setReportMarkdown(content);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [view.reportArtifactId, run?.report_artifact_id]);

  const handleUpload = useCallback(
    async (file: File) => {
      if (!sessionId) {
        throw new Error("session not ready");
      }
      const artifact = await uploadFile(sessionId, file);
      setArtifacts((prev) => [...prev, artifact]);
    },
    [sessionId],
  );

  const handleSubmit = useCallback(
    async (question: string) => {
      if (!sessionId) {
        throw new Error("session not ready");
      }
      dispatch({ type: "reset" });
      setReportMarkdown("");
      const created = await createRun(sessionId, {
        question,
        uploaded_artifact_ids: artifacts.map((item) => item.artifact_id),
        max_web_sources: 8,
      });
      setRun(created);
    },
    [sessionId, artifacts],
  );

  const handleCancel = useCallback(async () => {
    if (!run) {
      return;
    }
    const updated = await cancelRun(run.run_id);
    setRun(updated);
  }, [run]);

  const active =
    run?.status === "pending" || run?.status === "running" || run?.status === "cancelling";

  return (
    <div className="app-shell">
      <header className="brand-bar">
        <div>
          <h1>AI Dev Researcher</h1>
          <p>DeepAgents 多智能体调研：网页取证 + 文档分析 + 本地知识库</p>
        </div>
        <div>
          <span
            className={`status-pill ${run?.status === "failed" ? "danger" : active ? "warn" : ""}`}
            role="status"
            aria-live="polite"
          >
            {run ? `run: ${run.status}` : "待命"}
          </span>
        </div>
      </header>

      {bootError ? <p className="error-text">{bootError}</p> : null}

      <div className="workbench">
        <div style={{ display: "grid", gap: "1rem", alignContent: "start" }}>
          <ResearchBriefForm
            disabled={!sessionId || active}
            onSubmit={handleSubmit}
            onCancel={handleCancel}
            canCancel={Boolean(active)}
          />
          <UploadPanel artifacts={artifacts} disabled={!sessionId || active} onUpload={handleUpload} />
        </div>
        <div style={{ display: "grid", gap: "1rem", alignContent: "start" }}>
          <ReportViewer
            markdown={reportMarkdown}
            artifactId={view.reportArtifactId ?? run?.report_artifact_id ?? undefined}
            degraded={view.reportDegraded}
            reason={view.reportReason}
          />
          <AgentTimeline state={view} />
        </div>
        <div style={{ display: "grid", gap: "1rem", alignContent: "start" }}>
          <SourceLedger sources={view.sources} />
        </div>
      </div>
    </div>
  );
}
