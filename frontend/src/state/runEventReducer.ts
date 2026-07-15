import type { ResearchEvent } from "../domain/schemas";

export type ConnectionState = "idle" | "connecting" | "connected" | "reconnecting" | "offline";

export type RunViewState = {
  events: ResearchEvent[];
  lastSeq: number;
  connection: ConnectionState;
  todos: Array<{ id: string; content: string; status: string }>;
  sources: Array<{
    evidence_id: string;
    source_type: string;
    title: string;
    evidence_level: string;
  }>;
  reportArtifactId?: string;
};

export const initialRunViewState: RunViewState = {
  events: [],
  lastSeq: 0,
  connection: "idle",
  todos: [],
  sources: [],
};

export type RunViewAction =
  | { type: "reset" }
  | { type: "connection"; connection: ConnectionState }
  | { type: "events"; events: ResearchEvent[] };

export function runEventReducer(state: RunViewState, action: RunViewAction): RunViewState {
  switch (action.type) {
    case "reset":
      return { ...initialRunViewState };
    case "connection":
      return { ...state, connection: action.connection };
    case "events": {
      const merged = [...state.events];
      const seen = new Set(merged.map((item) => item.seq));
      for (const event of action.events) {
        if (event.type === "heartbeat" || event.seq < 0) {
          continue;
        }
        if (seen.has(event.seq)) {
          continue;
        }
        merged.push(event);
        seen.add(event.seq);
      }
      merged.sort((a, b) => a.seq - b.seq);
      const lastSeq = merged.reduce((max, item) => Math.max(max, item.seq), state.lastSeq);
      const todos = [...state.todos];
      const sources = [...state.sources];
      let reportArtifactId = state.reportArtifactId;
      for (const event of action.events) {
        if (event.type === "plan.updated" && Array.isArray(event.payload.items)) {
          todos.splice(0, todos.length, ...(event.payload.items as RunViewState["todos"]));
        }
        if (event.type === "source.discovered") {
          sources.push({
            evidence_id: String(event.payload.evidence_id ?? ""),
            source_type: String(event.payload.source_type ?? ""),
            title: String(event.payload.title ?? ""),
            evidence_level: String(event.payload.evidence_level ?? ""),
          });
        }
        if (event.type === "report.ready" || event.type === "run.succeeded") {
          reportArtifactId = String(
            event.payload.artifact_id ?? event.payload.report_artifact_id ?? reportArtifactId ?? "",
          );
          if (!reportArtifactId) {
            reportArtifactId = undefined;
          }
        }
      }
      return {
        ...state,
        events: merged,
        lastSeq,
        todos,
        sources,
        reportArtifactId,
      };
    }
    default:
      return state;
  }
}
