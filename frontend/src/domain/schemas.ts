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

// GET /api/sessions 列表项（含 display_name；SessionSchema 用于单条详情）
export const SessionListItemSchema = z.object({
  session_id: z.string().uuid(),
  display_name: z.string().nullable(),
  status: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const RunListResponseSchema = z.array(RunSchema);

// ---- 结构化报告（report-json）----
export const ResearchClaimSchema = z
  .object({
    id: z.string(),
    statement: z.string(),
    citation_ids: z.array(z.string()),
    confidence: z.enum(["high", "medium", "low"]),
  })
  .passthrough();

export const ReportTableSchema = z
  .object({
    columns: z.array(z.string()),
    rows: z.array(z.array(z.string())),
    citation_ids: z.array(z.string()),
  })
  .passthrough();

// 递归章节（含 subsections/table）——镜像 backend domain/reports.py ReportSection。
// 用显式 interface 承载递归（避免 z.infer 自引用环）。
export interface ReportSectionShape {
  heading: string;
  claims: ResearchClaim[];
  subsections?: ReportSectionShape[];
  table?: ReportTable | null;
}

export const ReportSectionSchema: z.ZodType<ReportSectionShape> = z.lazy(() =>
  z
    .object({
      heading: z.string(),
      claims: z.array(ResearchClaimSchema),
      subsections: z.array(ReportSectionSchema).optional(),
      table: ReportTableSchema.nullable().optional(),
    })
    .passthrough(),
);

export const DisagreementSideSchema = z
  .object({
    position: z.string(),
    citation_ids: z.array(z.string()),
  })
  .passthrough();

export const DisagreementSchema = z
  .object({
    topic: z.string(),
    claim_ids: z.array(z.string()),
    sides: z.array(DisagreementSideSchema),
  })
  .passthrough();

export const ResearchReportSchema = z
  .object({
    title: z.string(),
    executive_summary_claim_ids: z.array(z.string()),
    sections: z.array(ReportSectionSchema),
    disagreements: z.array(DisagreementSchema).optional().default([]),
    unknowns: z.array(z.string()).optional().default([]),
    recommendations: z.array(ResearchClaimSchema).optional().default([]),
  })
  .passthrough();

export const ReportJsonResponseSchema = z
  .object({
    artifact_id: z.string().uuid(),
    report: ResearchReportSchema.nullable(),
    degraded: z.boolean(),
    reason: z.string().nullable(),
  })
  .passthrough();

export type Session = z.infer<typeof SessionSchema>;
export type SessionListItem = z.infer<typeof SessionListItemSchema>;
export type Artifact = z.infer<typeof ArtifactSchema>;
export type Run = z.infer<typeof RunSchema>;
export type ResearchEvent = z.infer<typeof ResearchEventSchema>;
export type ResearchClaim = z.infer<typeof ResearchClaimSchema>;
export type ReportTable = z.infer<typeof ReportTableSchema>;
export type ReportSection = ReportSectionShape;
export type Disagreement = z.infer<typeof DisagreementSchema>;
export type ResearchReport = z.infer<typeof ResearchReportSchema>;
export type ReportJsonResponse = z.infer<typeof ReportJsonResponseSchema>;
