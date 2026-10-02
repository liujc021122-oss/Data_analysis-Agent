import { ArrowRight, Clock3 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { DataTable } from "@/components/ui/DataTable";
import { PageState } from "@/components/ui/PageState";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { taskStatusOptions, useTasks } from "@/hooks/useTasks";
import { errorMessage, formatDate } from "@/pages/pageUtils";
import type { AnalysisTaskResponse, TaskStatus } from "@/types/api";

export function TaskHistoryPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<TaskStatus | "">("");
  const [page, setPage] = useState(1);
  const { data, isLoading, isError, error, refetch } = useTasks({ status: status || undefined, page, pageSize: 20 });
  const hasRows = !isLoading && !isError && Boolean(data?.items.length);

  const columns = [
    { key: "query", header: "分析需求", render: (row: AnalysisTaskResponse) => <strong className="table-primary-text">{row.query}</strong> },
    { key: "status", header: "状态", render: (row: AnalysisTaskResponse) => <StatusBadge status={row.status} /> },
    { key: "created", header: "创建时间", render: (row: AnalysisTaskResponse) => <span className="table-muted-text">{formatDate(row.created_at)}</span> },
    { key: "updated", header: "最近更新", render: (row: AnalysisTaskResponse) => <span className="table-muted-text">{formatDate(row.updated_at)}</span> },
    { key: "action", header: "", render: () => <ArrowRight size={17} aria-hidden="true" /> },
  ];

  return (
    <section className="page-section">
      <div className="page-heading"><div><p className="eyebrow">分析记录</p><h1>历史任务</h1><p className="page-lede">查看任务状态、执行时间线和已生成的报告。</p></div><span className="history-count"><Clock3 size={16} aria-hidden="true" />共 {data?.total ?? 0} 个任务</span></div>
      <section className="surface-card" aria-labelledby="history-list-title">
        <div className="card-heading">
          <div><h2 id="history-list-title">任务列表</h2></div>
          <label className="compact-field" htmlFor="task-status">
            <span>筛选状态</span>
            <select id="task-status" value={status} onChange={(event) => { setStatus(event.target.value as TaskStatus | ""); setPage(1); }}>
              {taskStatusOptions.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
            </select>
          </label>
        </div>
        {isLoading ? <PageState kind="loading" title="正在加载历史任务" description="正在读取任务记录。" /> : null}
        {isError ? <PageState kind="error" title="任务记录暂时不可用" description={errorMessage(error, "无法读取历史任务，请稍后重试。")} action={{ label: "重试", onClick: () => void refetch() }} /> : null}
        {!isLoading && !isError && data?.items.length === 0 ? <PageState kind="empty" title="还没有历史任务" description="创建一次分析任务后，它会显示在这里。" /> : null}
        {hasRows ? (
          <>
            <DataTable columns={columns} rows={data?.items ?? []} getRowKey={(row) => row.task_id} onRowClick={(row) => navigate(`/tasks/${row.task_id}`)} />
            <div className="pagination">
              <button type="button" className="button button-ghost" disabled={page === 1} onClick={() => setPage((current) => current - 1)}>上一页</button>
              <span>第 {data?.page ?? page} 页</span>
              <button type="button" className="button button-ghost" disabled={!data?.has_next} onClick={() => setPage((current) => current + 1)}>下一页</button>
            </div>
          </>
        ) : null}
      </section>
    </section>
  );
}
