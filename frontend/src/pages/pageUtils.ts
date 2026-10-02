import { ApiError } from "@/services/api/client";

export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError && error.message ? error.message : fallback;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function taskProgress(status: string): number {
  const progress: Record<string, number> = {
    PENDING: 0,
    QUEUED: 0,
    RUNNING: 5,
    EXPLORING: 15,
    CLEANING: 30,
    ANALYZING: 55,
    VALIDATING: 75,
    REPORTING: 90,
    COMPLETED: 100,
  };
  return progress[status] ?? 0;
}
