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

export function AgentTimeline({ state }: Props) {
  return (
    <section className="panel">
      <h2>执行时间线</h2>
      <p className="muted">连接状态：{state.connection}</p>
      {state.todos.length ? (
        <>
          <h3>Todo</h3>
          <ul>
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
          <div className="timeline-item" key={`${event.event_id}-${event.seq}`}>
            <strong>#{event.seq}</strong> {labelFor(event)}
            <div className="muted">{event.actor}</div>
          </div>
        ))}
      </div>
    </section>
  );
}
