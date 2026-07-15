import { FormEvent, useState } from "react";

type Props = {
  disabled?: boolean;
  onSubmit: (question: string) => Promise<void>;
  onCancel?: () => Promise<void>;
  canCancel?: boolean;
};

export function ResearchBriefForm({ disabled, onSubmit, onCancel, canCancel }: Props) {
  const [question, setQuestion] = useState(
    "结合 DeepAgents 官方文档和我上传的学习笔记，分析 DeepAgents 与手写 LangGraph 在个人 Agent 项目中的适用边界，并给出两周开发建议。",
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (event.nativeEvent instanceof SubmitEvent) {
      // keep default path
    }
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(question.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="panel" onSubmit={handleSubmit}>
      <h2>研究简报</h2>
      <p className="muted">填写问题后启动一次研究运行。同一会话同时只允许一个 active run。</p>
      <label>
        研究问题
        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && event.nativeEvent.isComposing) {
              event.preventDefault();
            }
          }}
          disabled={disabled || submitting}
        />
      </label>
      <div className="actions">
        <button className="btn" type="submit" disabled={disabled || submitting || question.trim().length < 10}>
          {submitting ? "启动中…" : "启动研究"}
        </button>
        {canCancel ? (
          <button
            className="btn secondary"
            type="button"
            onClick={() => void onCancel?.()}
            disabled={submitting}
          >
            取消运行
          </button>
        ) : null}
      </div>
      {error ? <p className="error-text">{error}</p> : null}
    </form>
  );
}
