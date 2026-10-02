import { Link } from "react-router-dom";
import { PageState } from "@/components/ui/PageState";

export function NotFoundPage() {
  return <main className="standalone-state"><PageState kind="empty" title="页面未找到" description="这个地址不存在，返回工作台继续操作。" action={{ label: "返回数据集", onClick: () => { window.location.assign("/datasets"); } }} /><Link className="button button-ghost" to="/datasets">返回数据集</Link></main>;
}
