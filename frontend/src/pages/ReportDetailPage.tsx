import { ArrowLeft, Download, ExternalLink, FileText } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { PageState } from "@/components/ui/PageState";
import { useArtifact } from "@/hooks/useArtifacts";
import { errorMessage, formatDate } from "@/pages/pageUtils";

export function ReportDetailPage() {
  const navigate = useNavigate();
  const { artifactId } = useParams<{ artifactId: string }>();
  const { data: artifact, isLoading, isError, error, refetch } = useArtifact(artifactId);

  if (isLoading) return <section className="page-section"><PageState kind="loading" title="正在加载报告" description="正在读取报告元信息。" /></section>;
  if (isError || !artifact) return <section className="page-section"><PageState kind="error" title="报告暂时不可用" description={errorMessage(error, "无法读取报告，请稍后重试。")} action={{ label: "重试", onClick: () => void refetch() }} /></section>;

  return (
    <section className="page-section">
      <div className="page-heading"><div><button type="button" className="back-link" onClick={() => navigate(-1)}><ArrowLeft size={16} aria-hidden="true" />返回上一页</button><p className="eyebrow">报告详情</p><h1>{artifact.name}</h1></div><span className="format-chip">{artifact.format ?? artifact.artifact_type}</span></div>
      <section className="surface-card report-card" aria-labelledby="report-meta-title"><div className="report-icon"><FileText size={28} aria-hidden="true" /></div><div><p className="eyebrow">已授权产物</p><h2 id="report-meta-title">{artifact.description || "分析报告"}</h2><p className="muted-copy">创建于 {formatDate(artifact.created_at)}{artifact.mime_type ? ` · ${artifact.mime_type}` : ""}</p></div><div className="report-actions">{artifact.content_url ? <><a className="button button-primary" href={artifact.content_url} target="_blank" rel="noreferrer"><ExternalLink size={16} aria-hidden="true" />打开报告</a><a className="button button-secondary" href={artifact.content_url} download><Download size={16} aria-hidden="true" />下载</a></> : <Button disabled variant="secondary">报告内容暂不可用</Button>}</div></section>
      <p className="security-note">报告链接由 API 按当前用户授权生成，前端不会展示宿主机路径或存储 URI。</p>
    </section>
  );
}
