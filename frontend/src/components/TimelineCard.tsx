import { useState } from "react";
import type { ResearchEvent } from "../domain/schemas";
import type { RunViewState } from "../state/runEventReducer";

type Props = {
  state: RunViewState;
};

function labelFor(event: ResearchEvent): string {
  switch (event.type) {
    case "run.started":
      return "运行开始";
    case "plan.updated":
      return "计划更新";
    case "agent.started":
      return `子智能体启动 · ${String(event.payload.agent_name ?? "")}`;
    case "agent.completed":
      return `子智能体完成 · ${String(event.payload.agent_name ?? "")}`;
    case "source.discovered":
      return `发现来源 · ${String(event.payload.evidence_id ?? "")}`;
    case "evidence.recorded":
      return `记录证据 · ${String(event.payload.evidence_id ?? "")}`;
    case "tool.started":
      return `调用工具 · ${String(event.payload.tool_name ?? "")}`;
    case "tool.completed":
      return `工具完成 · ${String(event.payload.tool_name ?? "")}`;
    case "tool.failed":
      return `工具失败 · ${String(event.payload.tool_name ?? "")}`;
    case "report.ready":
      return "报告就绪";
    case "run.succeeded":
      return "运行成功";
    case "run.failed":
      return "运行失败";
    case "run.cancelling":
      return "正在取消";
    case "run.cancelled":
      return "已取消";
    default:
      return event.type;
  }
}

function typeClass(event: ResearchEvent): string {
  switch (event.type) {
    case "run.succeeded":
    case "agent.completed":
    case "tool.completed":
    case "report.ready":
    case "evidence.recorded":
      return "success";
    case "tool.started":
    case "tool.failed":
    case "source.discovered":
      return "search";
    case "run.failed":
      return "danger";
    default:
      return "info";
  }
}

function typeIcon(event: ResearchEvent): string {
  switch (event.type) {
    case "run.started":
      return "▶";
    case "plan.updated":
      return "📋";
    case "agent.started":
      return "👤";
    case "agent.completed":
      return "✓";
    case "source.discovered":
    case "evidence.recorded":
      return "🔍";
    case "tool.started":
      return "🛠";
    case "tool.completed":
      return "✓";
    case "tool.failed":
      return "✕";
    case "report.ready":
      return "📄";
    case "run.succeeded":
      return "✓";
    case "run.failed":
      return "✕";
    case "run.cancelling":
      return "⏸";
    case "run.cancelled":
      return "⏹";
    default:
      return "·";
  }
}

function actorClass(actor: string): string {
  if (actor.includes("orchestrator")) return "actor-orchestrator";
  if (actor.includes("web-researcher")) return "actor-web";
  if (actor.includes("document-analyst")) return "actor-doc";
  return "actor-system";
}

/** 展开详情：展示工具输入/输出摘要、查询词、URL/路径、行号、摘录。 */
function Details({ event }: { event: ResearchEvent }) {
  const payload = event.payload;
  const rows: Array<[string, string]> = [];
  if (payload.tool_name != null) {
    rows.push(["工具", String(payload.tool_name)]);
  }
  if (payload.tool_input) {
    rows.push(["输入摘要", String(payload.tool_input)]);
  }
  if (payload.output_summary != null && event.type === "tool.completed") {
    rows.push(["输出摘要", String(payload.output_summary)]);
  }
  if (payload.query != null) {
    rows.push(["查询词", String(payload.query)]);
  }
  if (payload.url != null) {
    rows.push(["URL", String(payload.url)]);
  }
  if (payload.path != null) {
    rows.push(["路径", String(payload.path)]);
  }
  if (payload.locator != null) {
    rows.push(["定位", String(payload.locator)]);
  }
  if (payload.artifact_id != null && event.type === "report.ready") {
    rows.push(["报告 ID", String(payload.artifact_id)]);
  }
  if (payload.message != null) {
    rows.push(["信息", String(payload.message)]);
  }
  if (payload.reason != null) {
    rows.push(["失败原因", String(payload.reason)]);
  }
  if (payload.code != null) {
    rows.push(["错误码", String(payload.code)]);
  }
  if (payload.agent_name != null) {
    rows.push(["智能体", String(payload.agent_name)]);
  }
  if (rows.length === 0) {
    return (
      <pre className="timeline-detail mono">{JSON.stringify(payload, null, 2)}</pre>
    );
  }
  return (
    <dl className="timeline-detail">
      {rows.map(([key, value]) => (
        <div className="detail-row" key={key}>
          <dt>{key}</dt>
          <dd className="mono detail-value">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function TimelineItem({ event }: { event: ResearchEvent }) {
  const [open, setOpen] = useState(false);
  const failureReason =
    event.type === "run.failed"
      ? String(event.payload.reason ?? event.payload.message ?? "")
      : "";
  const canExpand =
    event.type === "tool.started" ||
    event.type === "tool.completed" ||
    event.type === "source.discovered" ||
    event.type === "evidence.recorded" ||
    event.type === "agent.started" ||
    event.type === "agent.completed" ||
    event.type === "report.ready" ||
    event.type === "run.failed";
  return (
    <div
      className={`timeline-item ${typeClass(event)} ${actorClass(event.actor)}`}
    >
      <button
        type="button"
        className="timeline-item-row"
        onClick={() => (canExpand ? setOpen((v) => !v) : undefined)}
        aria-expanded={canExpand ? open : undefined}
      >
        <span className="timeline-icon" aria-hidden="true">
          {typeIcon(event)}
        </span>
        <span className="timeline-seq mono">#{event.seq}</span>
        <span className="timeline-label">{labelFor(event)}</span>
        <span className="timeline-actor">{event.actor}</span>
        {canExpand ? (
          <span className="timeline-chevron" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
        ) : null}
      </button>
      {failureReason ? (
        <div className="timeline-failure" role="alert">
          <span className="timeline-failure-label">失败原因</span>
          <span className="timeline-failure-text">{failureReason}</span>
        </div>
      ) : null}
      {open ? <Details event={event} /> : null}
    </div>
  );
}

function statusFor(state: RunViewState) {
  const text =
    state.connection === "connected"
      ? "研究中"
      : state.connection === "connecting"
        ? "连接中"
        : state.connection === "reconnecting"
          ? "重连中"
          : "待命";
  const warn = state.connection === "reconnecting" || state.connection === "connecting";
  return { text, warn };
}

export function TimelineCard({ state }: Props) {
  const { text, warn } = statusFor(state);
  return (
    <section className="glass-card timeline-card">
      <div className="timeline-expand">
        <div className="timeline-expand-inner">
          <div className="timeline-header">
            <h2>执行时间线</h2>
            <span
              className={`status-pill ${warn ? "warn" : ""}`}
              role="status"
              aria-live="polite"
            >
              <span className="status-dot" aria-hidden="true" />
              <span>{text}</span>
            </span>
          </div>
          {state.todos.length ? (
            <ul className="todo-list">
              {state.todos.map((item) => (
                <li key={item.id}>
                  <span className="mono" style={{ color: "var(--haze)" }}>
                    {item.status}
                  </span>{" "}
                  {item.content}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="timeline-list">
            {state.events.length === 0 ? (
              <p style={{ color: "var(--mist)", fontSize: "0.85rem" }}>等待事件…</p>
            ) : null}
            {state.events.map((event) => (
              <TimelineItem key={`${event.event_id}-${event.seq}`} event={event} />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
