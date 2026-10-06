import { ArrowRight, FileText, ShieldCheck } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/app/auth";
import { Button } from "@/components/ui/Button";
import { ApiError } from "@/services/api/client";

function redirectPath(state: unknown): string {
  const from = state && typeof state === "object" && "from" in state ? (state as { from?: unknown }).from : undefined;
  return typeof from === "string" && from.startsWith("/") && !from.startsWith("//") ? from : "/datasets";
}

function toLoginErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "INVALID_CREDENTIALS") {
    return "邮箱或密码错误";
  }
  if (error instanceof ApiError && (error.code === "NETWORK_ERROR" || error.code === "REQUEST_TIMEOUT")) {
    return "无法连接认证服务，请确认后端已启动。";
  }
  return "登录未完成，请稍后重试。";
}

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { status, login, isLoggingIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "authenticated") {
      navigate(redirectPath(location.state), { replace: true });
    }
  }, [location.state, navigate, status]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setError(null);
    try {
      await login({ email: email.trim(), password });
      navigate(redirectPath(location.state), { replace: true });
    } catch (caught) {
      setError(toLoginErrorMessage(caught));
    }
  };

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="auth-brand">
          <span className="brand-mark" aria-hidden="true"><FileText size={20} /></span>
          <span>数据分析工作台</span>
        </div>
        <div className="auth-heading">
          <p className="eyebrow">安全登录</p>
          <h1 id="login-title">进入你的分析工作区</h1>
          <p>登录后访问数据集、任务和报告页面。</p>
        </div>
        <form className="form-stack" onSubmit={handleSubmit}>
          <div className="field-group">
            <label htmlFor="email">邮箱</label>
            <input id="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" />
          </div>
          <div className="field-group">
            <label htmlFor="password">密码</label>
            <input id="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" />
          </div>
          {error ? <p className="field-error" role="alert">{error}</p> : null}
          <Button type="submit" disabled={isLoggingIn}>登录 <ArrowRight size={16} aria-hidden="true" /></Button>
        </form>
        <p className="auth-note"><ShieldCheck size={15} aria-hidden="true" />登录状态由后端 Session 维护，浏览器会使用登录 Cookie。</p>
      </section>
    </main>
  );
}
