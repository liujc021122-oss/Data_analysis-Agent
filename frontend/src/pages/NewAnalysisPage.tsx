import { ArrowRight, Check, Database } from "lucide-react";
import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useNotifications } from "@/app/notifications";
import { Button } from "@/components/ui/Button";
import { PageState } from "@/components/ui/PageState";
import { useDatasets } from "@/hooks/useDatasets";
import { useCreateTask } from "@/hooks/useTasks";
import { errorMessage, formatBytes } from "@/pages/pageUtils";

export function NewAnalysisPage() {
  const navigate = useNavigate();
  const { notify } = useNotifications();
  const { data, isLoading, isError, error, refetch } = useDatasets({ page: 1, pageSize: 100 });
  const create = useCreateTask();
  const [query, setQuery] = useState("");
  const [selectedDatasetIds, setSelectedDatasetIds] = useState<string[]>([]);
  const [validationError, setValidationError] = useState<string | null>(null);

  const toggleDataset = (datasetId: string): void => {
    setSelectedDatasetIds((current) => current.includes(datasetId) ? current.filter((id) => id !== datasetId) : [...current, datasetId]);
    setValidationError(null);
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!query.trim()) {
      setValidationError("请填写分析需求。");
      return;
    }
    if (selectedDatasetIds.length === 0) {
      setValidationError("请选择至少一个数据集。");
      return;
    }
    setValidationError(null);
    create.mutate({
      query: query.trim(),
      idempotency_key: crypto.randomUUID(),
      dataset_ids: selectedDatasetIds,
      max_rounds: 10,
      metadata: { source: "frontend" },
    }, {
      onSuccess: (result) => {
        notify({ kind: "success", message: "分析任务已创建。" });
        navigate(`/tasks/${result.task_id}`);
      },
    });
  };

  return (
    <section className="page-section">
      <div className="page-heading">
        <div><p className="eyebrow">任务配置</p><h1>新建分析</h1><p className="page-lede">告诉工作台你想从数据中了解什么，先选择一个或多个数据集。</p></div>
      </div>
      {isLoading ? <PageState kind="loading" title="正在读取数据集" description="准备分析所需的数据资源。" /> : null}
      {isError ? <PageState kind="error" title="数据集暂时不可用" description={errorMessage(error, "无法读取数据集，请稍后重试。")} action={{ label: "重试", onClick: () => void refetch() }} /> : null}
      {!isLoading && !isError ? (
        <form className="analysis-form" onSubmit={handleSubmit}>
          <section className="surface-card form-card" aria-labelledby="query-title">
            <div className="card-heading"><div><p className="eyebrow">第一步</p><h2 id="query-title">描述你的分析需求</h2></div><span className="step-chip">1 / 2</span></div>
            <div className="field-group"><label htmlFor="analysis-query">分析需求</label><textarea id="analysis-query" value={query} onChange={(event) => setQuery(event.target.value)} rows={5} placeholder="例如：分析近 12 个月的销售趋势，并指出增长最快的产品。" /><small>使用自然语言描述目标、范围和你关心的指标。</small></div>
          </section>
          <section className="surface-card form-card" aria-labelledby="datasets-title">
            <div className="card-heading"><div><p className="eyebrow">第二步</p><h2 id="datasets-title">选择数据集</h2></div><span className="step-chip">{selectedDatasetIds.length} 个已选</span></div>
            {data?.items.length === 0 ? <div className="inline-empty"><Database size={20} aria-hidden="true" /><div><strong>还没有可用数据集</strong><p>先上传一个 CSV 文件，再回来创建分析。</p></div><Link className="button button-secondary" to="/datasets">前往数据集</Link></div> : <div className="dataset-picker">{data?.items.map((dataset) => <label className={`dataset-option${selectedDatasetIds.includes(dataset.dataset_id) ? " dataset-option-selected" : ""}`} htmlFor={`dataset-${dataset.dataset_id}`} key={dataset.dataset_id}><input id={`dataset-${dataset.dataset_id}`} type="checkbox" aria-label={`选择 ${dataset.name}`} checked={selectedDatasetIds.includes(dataset.dataset_id)} onChange={() => toggleDataset(dataset.dataset_id)} /><span className="dataset-check" aria-hidden="true"><Check size={14} /></span><span className="dataset-option-copy"><strong>{dataset.name}</strong><small>{dataset.profile.row_count.toLocaleString("zh-CN")} 行 · {formatBytes(dataset.size_bytes)}</small></span></label>)}</div>}
            {validationError ? <p className="field-error" role="alert">{validationError}</p> : null}
          </section>
          <div className="form-actions"><Link className="button button-ghost" to="/datasets">返回数据集</Link><Button type="submit" loading={create.isPending}>创建分析任务 <ArrowRight size={16} aria-hidden="true" /></Button></div>
        </form>
      ) : null}
    </section>
  );
}
