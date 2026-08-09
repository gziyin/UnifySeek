import { useRef, useState } from "react";
import type { Artifact } from "../domain/schemas";

type Props = {
  artifacts: Artifact[];
  disabled?: boolean;
  onUpload: (file: File) => Promise<void>;
  open: boolean;
};

export function UploadSection({ artifacts, disabled, onUpload, open }: Props) {
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
    <div className={`upload-section ${open ? "open" : ""}`}>
      <div className="upload-section-inner">
        <div className="upload-dropzone">
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.docx,.md,.txt,text/plain,text/markdown,application/pdf"
            disabled={disabled}
            onChange={(event) => void handleFiles(event.target.files)}
          />
          <div>点击或拖拽上传 PDF / DOCX / MD / TXT</div>
          <div className="upload-hint">单文件 ≤ 10 MiB，每会话最多 5 个</div>
        </div>
        {pendingName ? (
          <div className="upload-hint" style={{ marginTop: "0.4rem" }}>
            正在上传：{pendingName}
          </div>
        ) : null}
        {error ? <p className="error-text">{error}</p> : null}
        {artifacts.length ? (
          <ul className="upload-list">
            {artifacts.map((item) => (
              <li key={item.artifact_id}>
                <span>{item.display_name}</span>
                <span className="mono" style={{ color: "var(--haze)" }}>
                  {item.parse_status}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
