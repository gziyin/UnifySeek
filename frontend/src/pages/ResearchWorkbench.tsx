import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  ApiError,
  cancelRun,
  createRun,
  createSession,
  getArtifactContent,
  getReportJson,
  getRun,
  getSession,
  listEvents,
  uploadFile,
} from "../api/client";
import type { Artifact, ResearchReport, Run } from "../domain/schemas";
import { ResearchEventSchema } from "../domain/schemas";
import { Background, readBgChoice } from "../components/Background";
import { TopBar } from "../components/TopBar";
import { MODE_MAX_SOURCES, QueryCard, type ResearchMode } from "../components/QueryCard";
import { TimelineCard } from "../components/TimelineCard";
import { ReportCard } from "../components/ReportCard";
import { Ledger } from "../components/Ledger";
import { HistoryDrawer } from "../components/HistoryDrawer";
import { initialRunViewState, runEventReducer } from "../state/runEventReducer";

const SESSION_KEY = "ai_dev_researcher.session_id";
const BG_KEY = "unifyseek.bg";

const EXAMPLES: Array<{ label: string; text: string }> = [
  {
    label: "🧪 框架选型调研",
    text: "对比 FastAPI、Express、Gin 在高并发场景下的性能表现，包括延迟、吞吐量和资源占用。",
  },
  {
    label: "📄 论文内容总结",
    text: "总结《Attention Is All You Need》论文的核心创新点和对后续研究的影响。",
  },
  {
    label: "🔍 技术事实核查",
    text: "核查 Python 3.13 是否真的比 3.12 快 60%，引用官方基准测试和独立评测。",
  },
  {
    label: "📊 竞品对比分析",
    text: "对比 LangGraph、CrewAI、AutoGen 三个 Agent 框架在架构、生态和适用场景上的区别。",
  },
];

export function ResearchWorkbench() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [reportMarkdown, setReportMarkdown] = useState<string>("");
  const [reportJson, setReportJson] = useState<ResearchReport | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [view, dispatch] = useReducer(runEventReducer, initialRunViewState);
  const [mode, setMode] = useState<ResearchMode>("medium");
  const [question, setQuestion] = useState("");
  const [bgChoice, setBgChoice] = useState<1 | 2>(() => readBgChoice());
  const [drawerOpen, setDrawerOpen] = useState(false);
  const historyButtonRef = useRef<HTMLButtonElement | null>(null);
  // lastSeq ref 供 WebSocket 重连时续接事件流
  const lastSeqRef = useRef<number>(0);
  useEffect(() => {
    lastSeqRef.current = view.lastSeq;
  }, [view.lastSeq]);

  // ---- session boot（原样保留）----
  useEffect(() => {
    let cancelled = false;
    async function boot() {
      try {
        const existing = localStorage.getItem(SESSION_KEY);
        if (existing) {
          try {
            await getSession(existing);
            if (!cancelled) {
              setSessionId(existing);
            }
          } catch (err) {
            if (err instanceof ApiError && err.code === "SESSION_NOT_FOUND") {
              localStorage.removeItem(SESSION_KEY);
              const session = await createSession();
              localStorage.setItem(SESSION_KEY, session.session_id);
              if (!cancelled) {
                setSessionId(session.session_id);
              }
            } else {
              throw err;
            }
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

  // ---- WS + hydrate + 轮询 + 退避重连（原样保留）----
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.run_id]);

  const reportArtifactId = view.reportArtifactId ?? run?.report_artifact_id ?? undefined;

  // ---- 报告 markdown 拉取（原样保留）----
  useEffect(() => {
    if (!reportArtifactId) {
      return;
    }
    let cancelled = false;
    void getArtifactContent(reportArtifactId).then((content) => {
      if (!cancelled) {
        setReportMarkdown(content);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [reportArtifactId]);

  // ---- 结构化报告 JSON（可选交互）----
  useEffect(() => {
    if (!reportArtifactId) {
      setReportJson(null);
      return;
    }
    let cancelled = false;
    void getReportJson(reportArtifactId)
      .then((res) => {
        if (!cancelled) setReportJson(res.report ?? null);
      })
      .catch(() => {
        if (!cancelled) setReportJson(null);
      });
    return () => {
      cancelled = true;
    };
  }, [reportArtifactId]);

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
    async (submittedQuestion: string, submittedMode: ResearchMode) => {
      if (!sessionId) {
        throw new Error("session not ready");
      }
      dispatch({ type: "reset" });
      setReportMarkdown("");
      setReportJson(null);
      const created = await createRun(sessionId, {
        question: submittedQuestion,
        uploaded_artifact_ids: artifacts.map((item) => item.artifact_id),
        max_web_sources: MODE_MAX_SOURCES[submittedMode],
      });
      setRun(created);
    },
    [sessionId, artifacts],
  );

  const handleCancel = useCallback(async () => {
    if (!run) {
      return;
    }
    // 乐观置为 cancelling，按钮立即解锁，不等待接口响应。
    setRun((prev) => (prev ? { ...prev, status: "cancelling" } : prev));
    try {
      const updated = await cancelRun(run.run_id);
      setRun(updated);
    } catch {
      // 保留 cancelling，依赖轮询 / WebSocket 收敛终态。
    }
  }, [run]);

  // ---- 历史抽屉恢复：设置会话与 run，由 run?.run_id effect 自动重连 WS / 拉报告 ----
  const handleRestore = useCallback(
    ({ sessionId: sid, run: restored }: { sessionId: string; run: Run }) => {
      setSessionId(sid);
      setRun(restored);
      dispatch({ type: "reset" });
      setReportMarkdown("");
      setReportJson(null);
      setDrawerOpen(false);
      historyButtonRef.current?.focus();
    },
    [],
  );

  const handleBgChange = useCallback((choice: 1 | 2) => {
    setBgChoice(choice);
    localStorage.setItem(BG_KEY, String(choice));
  }, []);

  const active =
    run?.status === "pending" || run?.status === "running" || run?.status === "cancelling";
  const reportReady = Boolean(reportArtifactId);
  const stageClass =
    (run ? " stage-running" : "") + (reportReady ? " stage-report" : "");

  return (
    <>
      <Background choice={bgChoice} />
      <TopBar
        bgChoice={bgChoice}
        onBgChange={handleBgChange}
        drawerOpen={drawerOpen}
        onOpenDrawer={() => setDrawerOpen(true)}
        historyRef={historyButtonRef}
      />
      <HistoryDrawer
        open={drawerOpen}
        onClose={() => {
          setDrawerOpen(false);
          historyButtonRef.current?.focus();
        }}
        onRestore={handleRestore}
      />

      <div className={`app-shell${stageClass}`}>
        <main className="main-stage">
          <div className="content-stack">
            <div className="brand">
              <div className="brand-logo">
                <div className="brand-mark">U</div>
              </div>
              <h1>UnifySeek</h1>
              <p>深度调研系统 · 证据优先 · 可追溯 · 可验证</p>
            </div>

            <QueryCard
              question={question}
              onQuestionChange={setQuestion}
              disabled={!sessionId || active}
              mode={mode}
              onModeChange={setMode}
              onSubmit={handleSubmit}
              onCancel={handleCancel}
              canCancel={Boolean(active)}
              artifacts={artifacts}
              onUpload={handleUpload}
              uploadDisabled={!sessionId || active}
            />

            <TimelineCard state={view} />

            <ReportCard
              markdown={reportMarkdown}
              artifactId={reportArtifactId}
              degraded={view.reportDegraded}
              reason={view.reportReason}
              reportJson={reportJson}
            />

            <Ledger sources={view.sources} />

            <div className="examples-label">试试这些例子 →</div>
            <div className="examples">
              {EXAMPLES.map((example) => (
                <button
                  key={example.label}
                  type="button"
                  className="example-tag"
                  onClick={() => setQuestion(example.text)}
                >
                  {example.label}
                </button>
              ))}
            </div>

            <p className="footer-note">UnifySeek 可能会出错 · 所有结论请结合原始来源核验</p>
          </div>
        </main>
      </div>

      {bootError ? <p className="error-text">{bootError}</p> : null}
    </>
  );
}
