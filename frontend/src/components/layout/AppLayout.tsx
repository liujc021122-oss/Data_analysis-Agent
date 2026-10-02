import { Database, FileText, History, LogOut, Menu, Plus, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { clearSession, getSession } from "@/services/session";

interface AppLayoutProps {
  children: ReactNode;
}

const navigation = [
  { to: "/datasets", label: "数据集", icon: Database },
  { to: "/analyses/new", label: "新建分析", icon: Plus },
  { to: "/tasks/history", label: "历史任务", icon: History },
];

function pageTitle(pathname: string): string {
  if (pathname === "/datasets") return "数据集";
  if (pathname === "/analyses/new") return "新建分析";
  if (pathname.startsWith("/tasks/")) return pathname === "/tasks/history" ? "历史任务" : "任务详情";
  if (pathname.startsWith("/reports/")) return "报告详情";
  return "工作台";
}

export function AppLayout({ children }: AppLayoutProps) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const session = getSession();

  const logout = (): void => {
    clearSession();
    setMobileNavOpen(false);
    navigate("/login", { replace: true });
  };

  return (
    <div className="app-shell">
      {mobileNavOpen ? <button type="button" className="nav-scrim" aria-label="关闭导航" onClick={() => setMobileNavOpen(false)} /> : null}
      <aside className={`app-sidebar${mobileNavOpen ? " app-sidebar-open" : ""}`} aria-label="主导航">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true"><FileText size={18} /></span>
          <span>
            <strong>数据分析</strong>
            <small>工作台</small>
          </span>
          <button type="button" className="icon-button mobile-close" aria-label="关闭导航" onClick={() => setMobileNavOpen(false)}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <nav className="app-nav">
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => `nav-link${isActive ? " nav-link-active" : ""}`} onClick={() => setMobileNavOpen(false)}>
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="user-summary">
            <span className="avatar" aria-hidden="true">{(session?.displayName || "开").slice(0, 1)}</span>
            <span className="user-copy">
              <strong>{session?.displayName || "开发用户"}</strong>
              <small>本地开发环境</small>
            </span>
          </div>
          <button type="button" className="logout-button" onClick={logout}>
            <LogOut size={16} aria-hidden="true" />
            <span>退出登录</span>
          </button>
        </div>
      </aside>
      <div className="app-main">
        <header className="app-topbar">
          <button type="button" className="icon-button mobile-menu" aria-label="打开导航" aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen(true)}>
            <Menu size={20} aria-hidden="true" />
          </button>
          <div className="topbar-title"><span>工作区</span><strong>{pageTitle(location.pathname)}</strong></div>
          <span className="topbar-status"><span className="status-dot" aria-hidden="true" />API 工作区</span>
        </header>
        <main className="app-content">{children}</main>
      </div>
    </div>
  );
}
