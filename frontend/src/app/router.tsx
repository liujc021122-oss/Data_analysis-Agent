import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { PropsWithChildren } from "react";
import { useAuth } from "@/app/auth";
import { AppLayout } from "@/components/layout/AppLayout";
import { PageState } from "@/components/ui/PageState";
import { DatasetsPage } from "@/pages/DatasetsPage";
import { LoginPage } from "@/pages/LoginPage";
import { NewAnalysisPage } from "@/pages/NewAnalysisPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ReportDetailPage } from "@/pages/ReportDetailPage";
import { TaskDetailPage } from "@/pages/TaskDetailPage";
import { TaskHistoryPage } from "@/pages/TaskHistoryPage";

export function ProtectedRoute({ children }: PropsWithChildren) {
  const location = useLocation();
  const { status, retryBootstrap } = useAuth();
  if (status === "loading") {
    return <PageState kind="loading" title="正在验证登录状态" description="请稍候。" />;
  }
  if (status === "error") {
    return <PageState kind="error" title="认证服务暂时不可用" description="请检查 API 服务后重试。" action={{ label: "重试", onClick: retryBootstrap }} />;
  }
  if (status === "unauthenticated") {
    const from = location.pathname + location.search + location.hash;
    return <Navigate to="/login" replace state={{ from }} />;
  }
  return <>{children}</>;
}

function ProtectedPage({ children }: PropsWithChildren) {
  return (
    <ProtectedRoute>
      <AppLayout>{children}</AppLayout>
    </ProtectedRoute>
  );
}

export function AppRouter() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/" element={<Navigate to="/datasets" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/datasets" element={<ProtectedPage><DatasetsPage /></ProtectedPage>} />
        <Route path="/analyses/new" element={<ProtectedPage><NewAnalysisPage /></ProtectedPage>} />
        <Route path="/tasks/history" element={<ProtectedPage><TaskHistoryPage /></ProtectedPage>} />
        <Route path="/tasks/:taskId" element={<ProtectedPage><TaskDetailPage /></ProtectedPage>} />
        <Route path="/reports/:artifactId" element={<ProtectedPage><ReportDetailPage /></ProtectedPage>} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </BrowserRouter>
  );
}
