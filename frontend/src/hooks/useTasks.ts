import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cancelTask, createTask, getTask, listTaskEvents, listTasks, retryTask, type PageParams, type TaskListParams } from "@/services/api/resources";
import type { AnalysisTaskCreateRequest, TaskStatus } from "@/types/api";

export const tasksKey = (params: TaskListParams = {}) => ["tasks", { status: params.status ?? "", page: params.page ?? 1, pageSize: params.pageSize ?? 20 }] as const;
export const taskKey = (taskId: string) => ["task", taskId] as const;
export const taskEventsKey = (taskId: string, params: PageParams = {}) => ["task-events", taskId, { page: params.page ?? 1, pageSize: params.pageSize ?? 20 }] as const;

export function useTasks(params: TaskListParams = {}) {
  return useQuery({ queryKey: tasksKey(params), queryFn: () => listTasks(params) });
}

export function useTask(taskId: string | undefined) {
  return useQuery({
    queryKey: taskKey(taskId ?? ""),
    queryFn: () => getTask(taskId as string),
    enabled: Boolean(taskId),
  });
}

export function useTaskEvents(taskId: string | undefined, params: PageParams = {}) {
  return useQuery({
    queryKey: taskEventsKey(taskId ?? "", params),
    queryFn: () => listTaskEvents(taskId as string, params),
    enabled: Boolean(taskId),
  });
}

export function useCreateTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: AnalysisTaskCreateRequest) => createTask(request),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.setQueryData(taskKey(result.task_id), undefined);
    },
  });
}

export function useCancelTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: cancelTask,
    onSuccess: (task) => {
      queryClient.setQueryData(taskKey(task.task_id), task);
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}

export function useRetryTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: retryTask,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: taskKey(result.task_id) });
    },
  });
}

export const taskStatusOptions: Array<{ value: TaskStatus | ""; label: string }> = [
  { value: "", label: "全部状态" },
  { value: "PENDING", label: "等待提交" },
  { value: "QUEUED", label: "排队中" },
  { value: "RUNNING", label: "运行中" },
  { value: "COMPLETED", label: "已完成" },
  { value: "FAILED", label: "失败" },
  { value: "CANCELLED", label: "已取消" },
];
