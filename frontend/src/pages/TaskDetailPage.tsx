import { ArrowLeft, ArrowRight, Ban, Clock3, FileText, RefreshCcw, RotateCcw } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useNotifications } from "@/app/notifications";
import { Button } from "@/components/ui/Button";
import { PageState } from "@/components/ui/PageState";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { useCancelTask, useRetryTask, useTask, useTaskEvents } from "@/hooks/useTasks";
import { errorMessage, formatDate, taskProgress } from "@/pages/pageUtils";

export function TaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const { notify } = useNotifications();
  const taskQuery = useTask(taskId);
  const eventsQuery = useTaskEvents(taskId, { page: 1, pageSize: 100 });
  const cancel = useCancelTask();
  const retry = useRetryTask();

  if (taskQuery.isLoading) return <section className="page-section"><PageState kind="loading" title="正在加载任务" description="正在读取任务状态和事件。" /></section>;
  if (taskQuery.isError || !taskQuery.data) return <section className="page-section"><PageState kind="error" title="任务暂时不可用" description={errorMessage(taskQuery.error, "无法读取任务详情，请稍后重试。")} action={{ label: "重试", onClick: () => { void taskQuery.refetch(); void eventsQuery.refetch(); } }} /></section>;

  const task = taskQuery.data;
  const progress = taskProgress(task.status);
  const canCancel = ["PENDING", "QUEUED", "RUNNING", "EXPLORING", "CLEANING", "ANALYZING", "VALIDATING", "REPORTING"].includes(task.status);
  const canRetry = task.status === "FAILED";

  return (
    <section className="page-section">
      <div className="page-heading"><div><Link className="back-link" to="/tasks/history"><ArrowLeft size={16} aria-hidden="true" />返回历史任务</Link><p className="eyebrow">任务详情</p><h1>{task.query}</h1></div><StatusBadge status={task.status} /></div>
      <div className="task-overview-grid">
        <section className="surface-card task-progress-card" aria-labelledby="progress-title"><div className="card-heading"><div><p className="eyebrow">当前进度</p><h2 id="progress-title">{task.status === "COMPLETED" ? "分析已完成" : "分析正在进行"}</h2></div><strong className="progress-value">{progress}%</strong></div><div className="progress-track" aria-label={`任务进度 ${progress}%`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><span style={{ width: `${progress}%` }} /></div><div className="task-meta"><span><Clock3 size={15} aria-hidden="true" />创建于 {formatDate(task.created_at)}</span><span><RefreshCcw size={15} aria-hidden="true" />更新于 {formatDate(task.updated_at)}</span></div>{task.error ? <p className="task-error" role="alert">{task.error.message}（{task.error.code}）</p> : null}<div className="task-actions">{canCancel ? <Button variant="danger" loading={cancel.isPending} onClick={() => cancel.mutate(task.task_id, { onSuccess: () => notify({ kind: "success", message: "任务已取消。" }) })}><Ban size={16} aria-hidden="true" />取消任务</Button> : null}{canRetry ? <Button variant="secondary" loading={retry.isPending} onClick={() => retry.mutate(task.task_id, { onSuccess: () => notify({ kind: "success", message: "任务已重新排队。" }) })}><RotateCcw size={16} aria-hidden="true" />重试任务</Button> : null}</div></section>
        <section className="surface-card" aria-labelledby="artifacts-title"><div className="card-heading"><div><p className="eyebrow">输出文件</p><h2 id="artifacts-title">报告与产物</h2></div><FileText size={22} aria-hidden="true" /></div>{task.artifacts.length === 0 ? <p className="muted-copy">任务完成后，报告和图表会显示在这里。</p> : <div className="artifact-list">{task.artifacts.map((artifact) => <Link className="artifact-row" to={`/reports/${artifact.artifact_id}`} key={artifact.artifact_id}><span className="artifact-icon"><FileText size={17} aria-hidden="true" /></span><span><strong>{artifact.name}</strong><small>{artifact.format ?? artifact.artifact_type}</small></span><ArrowRight className="artifact-arrow" size={16} aria-hidden="true" /></Link>)}</div>}</section>
      </div>
      <section className="surface-card timeline-card" aria-labelledby="timeline-title"><div className="card-heading"><div><p className="eyebrow">执行记录</p><h2 id="timeline-title">任务事件</h2></div></div>{eventsQuery.isLoading ? <p className="muted-copy">正在加载事件…</p> : eventsQuery.isError ? <PageState kind="error" title="事件暂时不可用" description="可以稍后重试读取事件。" action={{ label: "重试", onClick: () => void eventsQuery.refetch() }} /> : eventsQuery.data?.items.length === 0 ? <p className="muted-copy">暂无任务事件。</p> : <ol className="timeline">{eventsQuery.data?.items.map((event) => <li className="timeline-item" key={event.event_id}><span className="timeline-dot" aria-hidden="true" /><div><div className="timeline-heading"><strong>{event.stage || event.to_status}</strong><time>{formatDate(event.occurred_at)}</time></div><p>{event.message || "状态已更新"}</p></div></li>)}</ol>}</section>
    </section>
  );
}
