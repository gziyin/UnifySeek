import { useRef, useState } from "react";
import type { Artifact } from "../domain/schemas";

type Props = {
  artifacts: Artifact[];
  disabled?: boolean;
  onUpload: (file: File) => Promise<void>;
};

export function UploadPanel({ artifacts, disabled, onUpload }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [pendingName, setPendingName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleFiles(files: FileList | null) {
    if (!files?.length) {
      return;
    }
    setError(null);
    for (const file of Array.from(files)) {
      setPendingName(file.name);
      try {
        await onUpload(file);
      } catch (err) {
        setError(err instanceof Error ? err.message : `上传失败: ${file.name}`);
      }
    }
    setPendingName(null);
    if (inputRef.current) {
      inputRef.current.value = "";
    }
  }

  return (
    <section className="panel">
      <h2>上传资料</h2>
      <p className="muted">支持 PDF / DOCX / Markdown / TXT，单文件 ≤ 10 MiB，每会话最多 5 个。</p>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.docx,.md,.txt,text/plain,text/markdown,application/pdf"
        disabled={disabled}
        onChange={(event) => void handleFiles(event.target.files)}
      />
      {pendingName ? <p className="muted">正在上传：{pendingName}</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
      <ul className="upload-list">
        {artifacts.map((item) => (
          <li key={item.artifact_id}>
            <span>{item.display_name}</span>
            <span className="mono muted">{item.parse_status}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
