export type TaskStatus =
  | "PENDING"
  | "QUEUED"
  | "RUNNING"
  | "EXPLORING"
  | "CLEANING"
  | "ANALYZING"
  | "VALIDATING"
  | "REPORTING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type TaskEventType =
  | "STATUS_CHANGED"
  | "TOOL_CALLED"
  | "EXECUTION_COMPLETED"
  | "ARTIFACT_CREATED"
  | "ERROR"
  | "task.created"
  | "task.started"
  | "stage.started"
  | "llm.started"
  | "llm.completed"
  | "tool.started"
  | "tool.completed"
  | "chart.created"
  | "report.created"
  | "task.failed"
  | "task.completed";

export type ReportFormat = "MARKDOWN" | "HTML" | "DOCX";

export interface PageResponse<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  has_next: boolean;
}

export interface ApiErrorResponse {
  code: string;
  message: string;
  details: Record<string, unknown>;
  request_id?: string;
}

export interface ColumnProfile {
  name: string;
  inferred_type: "empty" | "boolean" | "integer" | "number" | "datetime" | "string" | "mixed";
  non_null_count: number;
  missing_count: number;
  missing_rate: number;
}

export interface SensitiveField {
  column_name: string;
  risk_type: string;
  detected_by: Array<"name" | "value">;
}

export interface DatasetProfile {
  encoding: string;
  delimiter: string;
  row_count: number;
  column_count: number;
  columns: ColumnProfile[];
  preview_rows: Array<Record<string, unknown>>;
  sensitive_fields: SensitiveField[];
}

export interface DatasetResponse {
  dataset_id: string;
  name: string;
  content_type: string;
  size_bytes: number;
  checksum: string | null;
  created_at: string;
  profile: DatasetProfile;
}

export interface DatasetUploadResponse {
  dataset_id: string;
  profile: DatasetProfile;
}

export interface ErrorResponse {
  code: string;
  message: string;
  details: Record<string, unknown>;
  request_id: string;
}

export interface ArtifactResponse {
  artifact_id: string;
  artifact_type: string;
  name: string;
  download_url: string | null;
  content_url: string | null;
  format: ReportFormat | null;
  mime_type: string | null;
  description: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface AnalysisTaskResponse {
  task_id: string;
  query: string;
  dataset_ids: string[];
  status: TaskStatus;
  max_rounds: number;
  created_at: string;
  updated_at: string;
  error: ErrorResponse | null;
  artifacts: ArtifactResponse[];
  metadata: Record<string, unknown>;
}

export interface AnalysisTaskCreateRequest {
  query: string;
  idempotency_key: string;
  dataset_ids: string[];
  max_rounds: number;
  metadata: Record<string, unknown>;
}

export interface TaskSubmissionResponse {
  task_id: string;
  status: TaskStatus;
  created: boolean;
  enqueued: boolean;
}

export interface TaskRetryResponse {
  task_id: string;
  status: TaskStatus;
  enqueued: boolean;
}

export interface TaskEventResponse {
  event_id: string;
  task_id: string;
  event_type: TaskEventType;
  from_status?: TaskStatus | null;
  to_status: TaskStatus;
  message?: string | null;
  occurred_at: string;
  metadata: Record<string, unknown>;
  sequence?: number;
  stage?: string | null;
  progress?: number | null;
}

export interface ArtifactDownloadResponse {
  artifact_id: string;
  download_url: string;
  content_url: string | null;
  expires_in: number;
}
