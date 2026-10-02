import { Database, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useNotifications } from "@/app/notifications";
import { Button } from "@/components/ui/Button";
import { DataTable } from "@/components/ui/DataTable";
import { FileUpload } from "@/components/ui/FileUpload";
import { Modal } from "@/components/ui/Modal";
import { PageState } from "@/components/ui/PageState";
import { useDatasets, useDeleteDataset, useUploadDataset } from "@/hooks/useDatasets";
import { formatBytes, formatDate, errorMessage } from "@/pages/pageUtils";
import type { DatasetResponse } from "@/types/api";

export function DatasetsPage() {
  const { data, isLoading, isError, error, refetch } = useDatasets({ page: 1, pageSize: 20 });
  const upload = useUploadDataset();
  const remove = useDeleteDataset();
  const { notify } = useNotifications();
  const [datasetToDelete, setDatasetToDelete] = useState<DatasetResponse | null>(null);

  const handleUpload = (file: File): void => {
    upload.mutate(file, {
      onSuccess: () => notify({ kind: "success", message: "数据集上传成功。" }),
    });
  };

  const confirmDelete = (): void => {
    if (!datasetToDelete) return;
    remove.mutate(datasetToDelete.dataset_id, {
      onSuccess: () => {
        notify({ kind: "success", message: "数据集已删除。" });
        setDatasetToDelete(null);
      },
    });
  };

  const columns = [
    { key: "name", header: "数据集", render: (row: DatasetResponse) => <strong>{row.name}</strong> },
    { key: "rows", header: "行数", render: (row: DatasetResponse) => row.profile.row_count.toLocaleString("zh-CN") },
    { key: "columns", header: "列数", render: (row: DatasetResponse) => row.profile.column_count },
    { key: "size", header: "大小", render: (row: DatasetResponse) => formatBytes(row.size_bytes) },
    { key: "created", header: "上传时间", render: (row: DatasetResponse) => formatDate(row.created_at) },
    {
      key: "actions",
      header: "操作",
      render: (row: DatasetResponse) => (
        <Button type="button" variant="ghost" aria-label={`删除 ${row.name}`} onClick={(event) => { event.stopPropagation(); setDatasetToDelete(row); }}>
          <Trash2 size={16} aria-hidden="true" />删除
        </Button>
      ),
    },
  ];

  return (
    <section className="page-section">
      <div className="page-heading">
        <div>
          <p className="eyebrow">数据准备</p>
          <h1>数据集</h1>
          <p className="page-lede">上传并管理用于分析的 CSV 数据。敏感字段会由后端进行识别和保护。</p>
        </div>
        <Link className="button button-primary" to="/analyses/new"><Database size={16} aria-hidden="true" />开始分析</Link>
      </div>
      <div className="content-grid content-grid-aside">
        <section className="surface-card upload-card" aria-labelledby="upload-title">
          <div className="card-heading">
            <div><p className="eyebrow">导入文件</p><h2 id="upload-title">上传新数据集</h2></div>
          </div>
          <FileUpload onFileSelected={handleUpload} disabled={upload.isPending} />
          {upload.isPending ? <p className="helper-text">正在上传并检查文件…</p> : null}
        </section>
        <section className="surface-card" aria-labelledby="dataset-list-title">
          <div className="card-heading"><div><p className="eyebrow">资源目录</p><h2 id="dataset-list-title">已上传数据</h2></div><span className="count-chip">{data?.total ?? 0} 个</span></div>
          {isLoading ? <PageState kind="loading" title="正在加载数据集" description="正在读取你的数据资源。" /> : null}
          {isError ? <PageState kind="error" title="数据集暂时不可用" description={errorMessage(error, "无法读取数据集，请稍后重试。")} action={{ label: "重试", onClick: () => void refetch() }} /> : null}
          {!isLoading && !isError && data?.items.length === 0 ? <PageState kind="empty" title="还没有数据集" description="上传一个 CSV 文件开始分析。" /> : null}
          {!isLoading && !isError && data && data.items.length > 0 ? <DataTable columns={columns} rows={data.items} getRowKey={(row) => row.dataset_id} /> : null}
        </section>
      </div>
      <Modal open={Boolean(datasetToDelete)} title="确认删除数据集" onClose={() => setDatasetToDelete(null)}>
        <p>删除后无法在工作台中继续使用“{datasetToDelete?.name}”。确定继续吗？</p>
        <div className="modal-actions"><Button variant="secondary" onClick={() => setDatasetToDelete(null)}>取消</Button><Button variant="danger" loading={remove.isPending} onClick={confirmDelete}>确认删除</Button></div>
      </Modal>
    </section>
  );
}
