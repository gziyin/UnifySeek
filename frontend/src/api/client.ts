import {
  ArtifactSchema,
  ResearchEventSchema,
  RunSchema,
  SessionSchema,
  type Artifact,
  type ResearchEvent,
  type Run,
  type Session,
} from "../domain/schemas";

export class ApiError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(`${code}: ${message}`);
    this.name = "ApiError";
    this.code = code;
  }
}

async function parseJson<T>(response: Response, schema: { parse: (data: unknown) => T }): Promise<T> {
  const data = await response.json();
  if (!response.ok) {
    const message = typeof data?.message === "string" ? data.message : response.statusText;
    const code = typeof data?.code === "string" ? data.code : "HTTP_ERROR";
    throw new ApiError(code, message);
  }
  return schema.parse(data);
}

export async function createSession(): Promise<Session> {
  const response = await fetch("/api/sessions", { method: "POST" });
  return parseJson(response, SessionSchema);
}

export async function getSession(sessionId: string): Promise<Session> {
  const response = await fetch(`/api/sessions/${sessionId}`);
  return parseJson(response, SessionSchema);
}

export async function uploadFile(sessionId: string, file: File): Promise<Artifact> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`/api/sessions/${sessionId}/uploads`, {
    method: "POST",
    body: form,
  });
  return parseJson(response, ArtifactSchema);
}

export async function createRun(
  sessionId: string,
  body: {
    question: string;
    uploaded_artifact_ids: string[];
    max_web_sources?: number;
  },
): Promise<Run> {
  const response = await fetch(`/api/sessions/${sessionId}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseJson(response, RunSchema);
}

export async function getRun(runId: string): Promise<Run> {
  const response = await fetch(`/api/runs/${runId}`);
  return parseJson(response, RunSchema);
}

export async function cancelRun(runId: string): Promise<Run> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 10_000);
  try {
    const response = await fetch(`/api/runs/${runId}/cancel`, {
      method: "POST",
      signal: controller.signal,
    });
    return parseJson(response, RunSchema);
  } finally {
    window.clearTimeout(timer);
  }
}

export async function listEvents(runId: string, afterSeq = 0): Promise<ResearchEvent[]> {
  const response = await fetch(`/api/runs/${runId}/events?after_seq=${afterSeq}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.message ?? "failed to load events");
  }
  return (data.events as unknown[]).map((item) => ResearchEventSchema.parse(item));
}

export async function getArtifactContent(artifactId: string): Promise<string> {
  const response = await fetch(`/api/artifacts/${artifactId}/content`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.message ?? "failed to load artifact");
  }
  return String(data.content ?? "");
}

export function artifactDownloadUrl(artifactId: string): string {
  return `/api/artifacts/${artifactId}/download`;
}
