import { apiClient } from "@/services/api/client";
import type {
  AnalysisTaskCreateRequest,
  AnalysisTaskResponse,
  ArtifactResponse,
  DatasetResponse,
  DatasetUploadResponse,
  PageResponse,
  TaskEventResponse,
  TaskRetryResponse,
  TaskStatus,
  TaskSubmissionResponse,
} from "@/types/api";

export interface PageParams {
  page?: number;
  pageSize?: number;
}

export interface TaskListParams extends PageParams {
  status?: TaskStatus;
}

function pageQuery({ page = 1, pageSize = 20 }: PageParams): string {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  return params.toString();
}

export function listDatasets(params: PageParams = {}): Promise<PageResponse<DatasetResponse>> {
  return apiClient.get(`/datasets?${pageQuery(params)}`);
}

export function uploadDataset(file: File): Promise<DatasetUploadResponse> {
  const body = new FormData();
  body.append("file", file);
  return apiClient.post("/datasets", body);
}

export function deleteDataset(datasetId: string): Promise<void> {
  return apiClient.delete(`/datasets/${encodeURIComponent(datasetId)}`);
}

export function listTasks({ status, page = 1, pageSize = 20 }: TaskListParams = {}): Promise<PageResponse<AnalysisTaskResponse>> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (status) {
    params.set("status", status);
  }
  return apiClient.get(`/analysis-tasks?${params.toString()}`);
}

export function getTask(taskId: string): Promise<AnalysisTaskResponse> {
  return apiClient.get(`/analysis-tasks/${encodeURIComponent(taskId)}`);
}

export function createTask(request: AnalysisTaskCreateRequest): Promise<TaskSubmissionResponse> {
  return apiClient.post("/analysis-tasks", request);
}

export function listTaskEvents(taskId: string, params: PageParams = {}): Promise<PageResponse<TaskEventResponse>> {
  return apiClient.get(`/analysis-tasks/${encodeURIComponent(taskId)}/events?${pageQuery(params)}`);
}

export function cancelTask(taskId: string): Promise<AnalysisTaskResponse> {
  return apiClient.post(`/analysis-tasks/${encodeURIComponent(taskId)}/cancel`);
}

export function retryTask(taskId: string): Promise<TaskRetryResponse> {
  return apiClient.post(`/analysis-tasks/${encodeURIComponent(taskId)}/retry`);
}

export function getArtifact(artifactId: string): Promise<ArtifactResponse> {
  return apiClient.get(`/artifacts/${encodeURIComponent(artifactId)}`);
}
