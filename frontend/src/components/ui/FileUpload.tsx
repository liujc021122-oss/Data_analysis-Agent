import { FileUp, UploadCloud } from "lucide-react";
import { useId, useRef, useState, type DragEvent, type ChangeEvent } from "react";

const DEFAULT_MAX_SIZE_BYTES = 50 * 1024 * 1024;

export interface FileUploadProps {
  onFileSelected: (file: File) => void;
  onError?: (message: string) => void;
  accept?: string;
  maxSizeBytes?: number;
  disabled?: boolean;
}

function isCsv(file: File): boolean {
  return file.name.toLowerCase().endsWith(".csv") || file.type === "text/csv";
}

function formatSize(bytes: number): string {
  return `${Math.round(bytes / 1024 / 1024)} MB`;
}

export function FileUpload({
  onFileSelected,
  onError,
  accept = ".csv,text/csv",
  maxSizeBytes = DEFAULT_MAX_SIZE_BYTES,
  disabled = false,
}: FileUploadProps) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reject = (message: string): void => {
    setError(message);
    onError?.(message);
  };

  const validate = (file: File): void => {
    if (!isCsv(file)) {
      reject("请选择 CSV 格式的数据文件。");
      return;
    }
    if (file.size > maxSizeBytes) {
      reject(`文件过大，请选择不超过 ${formatSize(maxSizeBytes)} 的文件。`);
      return;
    }
    setError(null);
    setFileName(file.name);
    onFileSelected(file);
  };

  const handleChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    if (file) {
      validate(file);
    }
  };

  const handleDrop = (event: DragEvent<HTMLLabelElement>): void => {
    event.preventDefault();
    if (!disabled) {
      const file = event.dataTransfer.files[0];
      if (file) {
        validate(file);
      }
    }
  };

  return (
    <div className="file-upload">
      <label
        className={`file-upload-zone${disabled ? " file-upload-disabled" : ""}`}
        htmlFor={inputId}
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
      >
        <input ref={inputRef} id={inputId} className="visually-hidden" type="file" accept={accept} disabled={disabled} onChange={handleChange} aria-label="选择数据集文件" />
        <span className="file-upload-icon" aria-hidden="true"><UploadCloud size={24} /></span>
        <strong>拖拽 CSV 文件到这里</strong>
        <span>或点击选择文件，单个文件最大 {formatSize(maxSizeBytes)}</span>
        <span className="file-upload-action"><FileUp size={16} aria-hidden="true" />选择文件</span>
      </label>
      {fileName ? <p className="file-upload-name" role="status">已选择：{fileName}</p> : null}
      {error ? <p className="field-error" role="alert">{error}</p> : null}
    </div>
  );
}
