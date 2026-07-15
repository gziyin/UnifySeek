import { z } from "zod";

export const SessionSchema = z.object({
  session_id: z.string().uuid(),
  status: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const ArtifactSchema = z.object({
  artifact_id: z.string().uuid(),
  session_id: z.string().uuid(),
  run_id: z.string().uuid().nullable().optional(),
  kind: z.string(),
  display_name: z.string(),
  mime_type: z.string(),
  size_bytes: z.number(),
  parse_status: z.string(),
  created_at: z.string(),
});

export const RunSchema = z.object({
  run_id: z.string().uuid(),
  session_id: z.string().uuid(),
  status: z.enum([
    "pending",
    "running",
    "succeeded",
    "failed",
    "interrupted",
    "cancelling",
    "cancelled",
  ]),
  question: z.string(),
  created_at: z.string(),
  started_at: z.string().nullable().optional(),
  finished_at: z.string().nullable().optional(),
  error_code: z.string().nullable().optional(),
  error_message: z.string().nullable().optional(),
  report_artifact_id: z.string().uuid().nullable().optional(),
  last_seq: z.number(),
});

export const ResearchEventSchema = z.object({
  protocol_version: z.literal("1.0"),
  event_id: z.string().uuid(),
  seq: z.number(),
  session_id: z.string().uuid(),
  run_id: z.string().uuid(),
  type: z.string(),
  occurred_at: z.string(),
  actor: z.string(),
  payload: z.record(z.any()),
});

export type Session = z.infer<typeof SessionSchema>;
export type Artifact = z.infer<typeof ArtifactSchema>;
export type Run = z.infer<typeof RunSchema>;
export type ResearchEvent = z.infer<typeof ResearchEventSchema>;
