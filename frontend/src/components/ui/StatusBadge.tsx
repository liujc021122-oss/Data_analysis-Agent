import type { TaskStatus } from "@/types/api";

const labels: Record<TaskStatus, string> = {
  PENDING: "等待提交",
  QUEUED: "排队中",
  RUNNING: "运行中",
  EXPLORING: "探索中",
  CLEANING: "清洗中",
  ANALYZING: "分析中",
  VALIDATING: "校验中",
  REPORTING: "生成报告",
  COMPLETED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
};

export function StatusBadge({ status }: { status: TaskStatus }) {
  return <span className={`status-badge status-${status.toLowerCase()}`}><span className="status-badge-dot" aria-hidden="true" />{labels[status]}</span>;
}
