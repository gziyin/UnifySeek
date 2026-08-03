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
  const canExpand =
    event.type === "tool.started" ||
    event.type === "tool.completed" ||
    event.type === "source.discovered" ||
    event.type === "evidence.recorded" ||
    event.type === "agent.started" ||
    event.type === "agent.completed" ||
    event.type === "report.ready";
  return (
    <div className={`timeline-item ${actorClass(event.actor)}`}>
      <button
        type="button"
        className={`timeline-head ${canExpand ? "expandable" : ""}`}
        onClick={() => (canExpand ? setOpen((v) => !v) : undefined)}
        aria-expanded={open}
      >
        <span className="timeline-seq mono">#{event.seq}</span>
        <span className="timeline-label">{labelFor(event)}</span>
        <span className="timeline-actor">{event.actor}</span>
        {canExpand ? <span className="timeline-chevron">{open ? "▾" : "▸"}</span> : null}
      </button>
      {open ? <Details event={event} /> : null}
    </div>
  );
}

export function AgentTimeline({ state }: Props) {
  return (
    <section className="panel">
      <h2>执行时间线</h2>
      <p className="muted">
        连接状态：{state.connection}
        {state.events.length ? ` · ${state.events.length} 条事件` : ""}
      </p>
      {state.todos.length ? (
        <>
          <h3>Todo</h3>
          <ul className="todo-list">
            {state.todos.map((item) => (
              <li key={item.id}>
                <span className="mono">{item.status}</span> {item.content}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <div className="timeline">
        {state.events.map((event) => (
          <TimelineItem key={`${event.event_id}-${event.seq}`} event={event} />
        ))}
      </div>
    </section>
  );
}
