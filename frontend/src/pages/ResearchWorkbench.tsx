import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  ApiError,
  cancelRun,
  createRun,
  createSession,
  deleteArtifact,
  getArtifactContent,
  getReportJson,
  getRun,
  getSession,
  listEvents,
  uploadFile,
} from "../api/client";
import type { Artifact, ResearchReport, Run } from "../domain/schemas";
import { ResearchEventSchema } from "../domain/schemas";
import { extractCitedEvidenceIds } from "../domain/reportCites";
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
  const [completedTip, setCompletedTip] = useState(false);
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
        // 同步推进 lastSeqRef，避免 connect() 在同步 ref 的 effect 运行前读到旧值，
        // 导致初始 after_seq=0 全量重放（#42 重连/hydrate 竞态）。
        lastSeqRef.current = events.reduce(
          (max, event) => Math.max(max, event.seq),
          lastSeqRef.current,
        );
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
          const data = parsed.data;
          if (data.type === "heartbeat") {
            // 心跳携带服务端时钟 server_time，用于校准客户端↔服务端 wall-clock 偏移（#42）。
            const serverTime = data.payload?.server_time;
            if (typeof serverTime === "string") {
              const serverTimeMs = Date.parse(serverTime);
              if (!Number.isNaN(serverTimeMs)) {
                dispatch({ type: "clockSync", serverTimeMs });
              }
            }
            return;
          }
          // 实时事件（非 hydrate）的 occurred_at 即服务端 publish 时刻，同样可校准；
          // hydrate 走上方 listEvents→events dispatch，其历史时间戳不用于校准。
          const occurredMs = Date.parse(data.occurred_at);
          if (!Number.isNaN(occurredMs)) {
            dispatch({ type: "clockSync", serverTimeMs: occurredMs });
          }
          dispatch({ type: "events", events: [data] });
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
          // 研究完成引导提示（#35）：显示横幅，引导用户点「＋ 新建对话」开始下一项研究。
          setCompletedTip(true);
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

  // 批次D：报告终态后才拉到 reportJson（进行中为 null）。抽取「被引用证据」有序列表
  // 下传给账本，主视图与报告 Sources 严格对齐；未就绪时为空数组 → Ledger 回退全量。
  const citedIds = useMemo(
    () => (reportJson ? extractCitedEvidenceIds(reportJson) : []),
    [reportJson],
  );

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

  const handleDelete = useCallback(
    async (artifactId: string) => {
      if (!sessionId) {
        throw new Error("session not ready");
      }
      await deleteArtifact(sessionId, artifactId);
      setArtifacts((prev) => prev.filter((item) => item.artifact_id !== artifactId));
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
      setCompletedTip(false);
      const created = await createRun(sessionId, {
        question: submittedQuestion,
        uploaded_artifact_ids: artifacts.map((item) => item.artifact_id),
        max_web_sources: MODE_MAX_SOURCES[submittedMode],
      });
      // F1(#41)：提交即乐观激活规划阶段（进行中 + 计时走动），不等首事件回显。
      // 幂等：reducer 仅在 plan 仍 pending 时置位；真实 run.started 不会覆盖。
      dispatch({ type: "optimisticStart", at: Date.now() });
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
      setCompletedTip(false);
      setDrawerOpen(false);
      historyButtonRef.current?.focus();
    },
    [],
  );

  // 删除会话后，若删除的是当前激活会话，则清空工作区并移除 SESSION_KEY（#33）。
  // 不自动新建会话，由用户在历史记录中点击「＋ 新建对话」（符合 #35 精神）。
  const handleDeleteSession = useCallback(
    (deletedId: string) => {
      if (sessionId !== deletedId) {
        return;
      }
      localStorage.removeItem(SESSION_KEY);
      setSessionId(null);
      setRun(null);
      setReportMarkdown("");
      setReportJson(null);
      setArtifacts([]);
      setQuestion("");
      setCompletedTip(false);
      dispatch({ type: "reset" });
    },
    [sessionId],
  );

  // 新建对话：创建新会话并清空工作区，不做自动跳转（#35）。
  const handleNewChat = useCallback(async () => {
    try {
      const ns = await createSession();
      localStorage.setItem(SESSION_KEY, ns.session_id);
      setSessionId(ns.session_id);
      dispatch({ type: "reset" });
      setRun(null);
      setReportMarkdown("");
      setReportJson(null);
      setArtifacts([]);
      setQuestion("");
      setCompletedTip(false);
      setDrawerOpen(false);
    } catch {
      // 创建失败保留现状。
    }
  }, []);

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
        onDeleteSession={handleDeleteSession}
        onNewChat={() => void handleNewChat()}
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
              onDelete={handleDelete}
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

            {completedTip ? (
              <div className="completed-tip" role="status">
                ✅ 本次研究已完成，可点击历史记录中的「＋ 新建对话」开始下一项研究。
              </div>
            ) : null}

            <Ledger sources={view.sources} citedIds={citedIds} />

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
