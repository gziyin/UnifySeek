import { FormEvent, useState } from "react";
import type { Artifact } from "../domain/schemas";
import {
  OUTPUT_MODE_LABELS,
  OUTPUT_MODE_MAX_SOURCES,
  type OutputMode,
} from "../domain/outputMode";
import { UploadSection } from "./UploadSection";

// 兼容别名：长度模式统一收敛到 domain/outputMode 的 OutputMode。
export type ResearchMode = OutputMode;

export const MODE_LABELS: Record<ResearchMode, string> = OUTPUT_MODE_LABELS;

export const MODE_MAX_SOURCES: Record<ResearchMode, number> = OUTPUT_MODE_MAX_SOURCES;

type Props = {
  question: string;
  onQuestionChange: (question: string) => void;
  disabled?: boolean;
  mode: OutputMode;
  onModeChange: (mode: OutputMode) => void;
  onSubmit: (question: string, mode: OutputMode) => Promise<void>;
  onCancel?: () => Promise<void>;
  canCancel?: boolean;
  artifacts: Artifact[];
  onUpload: (file: File) => Promise<void>;
  onDelete: (artifactId: string) => Promise<void>;
  uploadDisabled?: boolean;
};

export function QueryCard({
  question,
  onQuestionChange,
  disabled,
  mode,
  onModeChange,
  onSubmit,
  onCancel,
  canCancel,
  artifacts,
  onUpload,
  onDelete,
  uploadDisabled,
}: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const isEmpty = question.trim().length === 0;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(question.trim(), mode);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="glass-card query-card" onSubmit={handleSubmit}>
      <textarea
        value={question}
        onChange={(event) => onQuestionChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && event.nativeEvent.isComposing) {
            event.preventDefault();
          }
        }}
        placeholder="输入你的研究问题，例如：FastAPI 和 Express 在生产环境下的性能对比如何？"
        disabled={disabled || submitting}
        aria-label="研究问题"
      />

      <div className="query-actions">
        <div className="query-actions-left">
          <div className="mode-toggle" role="group" aria-label="输出长度模式">
            {(Object.keys(MODE_LABELS) as OutputMode[]).map((m) => (
              <button
                key={m}
                type="button"
                className={mode === m ? "active" : ""}
                onClick={() => onModeChange(m)}
                aria-pressed={mode === m}
              >
                {MODE_LABELS[m]}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="icon-btn"
            title={uploadOpen ? "收起上传" : "上传资料"}
            onClick={() => setUploadOpen((v) => !v)}
            aria-expanded={uploadOpen}
            aria-controls="upload-section"
          >
            <span aria-hidden="true">📎</span>
          </button>
        </div>
        {canCancel ? (
          <button type="button" className="cancel-btn" onClick={() => void onCancel?.()}>
            取消运行
          </button>
        ) : (
          <button type="submit" className="send-btn" disabled={disabled || submitting || isEmpty}>
            <span>{submitting ? "启动中…" : "开始研究"}</span>
            <span aria-hidden="true">↑</span>
          </button>
        )}
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      <div id="upload-section">
        <UploadSection
          artifacts={artifacts}
          disabled={uploadDisabled || disabled || submitting}
          onUpload={onUpload}
          onDelete={onDelete}
          open={uploadOpen}
        />
      </div>
    </form>
  );
}
