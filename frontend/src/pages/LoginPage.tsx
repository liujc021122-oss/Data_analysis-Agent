import { ArrowRight, FileText, ShieldCheck } from "lucide-react";
import { FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { DEFAULT_DEV_USER_ID, isValidUserId, saveSession } from "@/services/session";

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [userId, setUserId] = useState(DEFAULT_DEV_USER_ID);
  const [displayName, setDisplayName] = useState("演示分析员");
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!isValidUserId(userId)) {
      setError("请输入有效的 UUID 格式开发用户 ID。");
      return;
    }
    if (!displayName.trim()) {
      setError("请输入显示名称。");
      return;
    }
    saveSession({ userId, displayName });
    const from = (location.state as { from?: string } | null)?.from;
    navigate(from || "/datasets", { replace: true });
  };

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="auth-brand">
          <span className="brand-mark" aria-hidden="true"><FileText size={20} /></span>
          <span>数据分析工作台</span>
        </div>
        <div className="auth-heading">
          <p className="eyebrow">本地开发模式</p>
          <h1 id="login-title">进入你的分析工作区</h1>
          <p>使用开发身份访问数据集、任务和报告页面。此页面不会调用真实模型。</p>
        </div>
        <form className="form-stack" onSubmit={handleSubmit}>
          <div className="field-group">
            <label htmlFor="dev-user-id">开发用户 UUID</label>
            <input id="dev-user-id" value={userId} onChange={(event) => setUserId(event.target.value)} autoComplete="off" />
            <small>开发 API 会把它作为 X-User-ID 请求头。</small>
          </div>
          <div className="field-group">
            <label htmlFor="display-name">显示名称</label>
            <input id="display-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="name" />
          </div>
          {error ? <p className="field-error" role="alert">{error}</p> : null}
          <Button type="submit">进入工作台 <ArrowRight size={16} aria-hidden="true" /></Button>
        </form>
        <p className="auth-note"><ShieldCheck size={15} aria-hidden="true" />仅用于本地开发和 API 联调</p>
      </section>
    </main>
  );
}
