import { useRef, useState, type DragEvent } from "react";
import type { Artifact } from "../domain/schemas";

export function isFileDrag(types: ArrayLike<string>): boolean {
  return Array.from(types).includes("Files");
}

export async function uploadFilesSequentially(
  files: readonly File[],
  onUpload: (file: File) => Promise<void>,
): Promise<void> {
  for (const file of files) {
    await onUpload(file);
  }
}

type Props = {
  artifacts: Artifact[];
  disabled?: boolean;
  onUpload: (file: File) => Promise<void>;
  onDelete: (artifactId: string) => Promise<void>;
  open: boolean;
};

export function UploadSection({ artifacts, disabled, onUpload, onDelete, open }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const dragDepthRef = useRef(0);
  const [pendingName, setPendingName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);

  async function handleFiles(files: FileList | null) {
    if (!files?.length) {
      return;
    }
    setError(null);
    await uploadFilesSequentially(Array.from(files), async (file) => {
      setPendingName(file.name);
      try {
        await onUpload(file);
      } catch (err) {
        setError(err instanceof Error ? err.message : `上传失败: ${file.name}`);
      }
    });
    setPendingName(null);
    if (inputRef.current) {
      inputRef.current.value = "";
    }
  }

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!isFileDrag(event.dataTransfer.types)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current += 1;
    if (!disabled) {
      setDragActive(true);
    }
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (!isFileDrag(event.dataTransfer.types)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = disabled ? "none" : "copy";
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!isFileDrag(event.dataTransfer.types)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setDragActive(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    if (!isFileDrag(event.dataTransfer.types)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = 0;
    setDragActive(false);
    if (!disabled) {
      void handleFiles(event.dataTransfer.files);
    }
  }

  return (
    <div className={`upload-section ${open ? "open" : ""}`}>
      <div className="upload-section-inner">
        <div
          className={`upload-dropzone ${dragActive ? "drag-active" : ""} ${disabled ? "disabled" : ""}`}
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.md,.txt,text/plain,text/markdown,application/pdf"
            disabled={disabled}
            onChange={(event) => void handleFiles(event.target.files)}
          />
          <div>点击或拖拽上传 PDF / DOCX / MD / TXT</div>
          <div className="upload-hint">单文件 ≤ 50 MiB，每会话最多 5 个</div>
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
                <span className="upload-name">{item.display_name}</span>
                <span className="mono" style={{ color: "var(--haze)" }}>
                  {item.parse_status}
                </span>
                <button
                  type="button"
                  className="upload-remove"
                  title={`删除 ${item.display_name}`}
                  aria-label={`删除 ${item.display_name}`}
                  disabled={disabled}
                  onClick={() => void onDelete(item.artifact_id)}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
