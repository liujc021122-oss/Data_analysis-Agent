import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { PropsWithChildren } from "react";
import { AppLayout } from "@/components/layout/AppLayout";
import { DatasetsPage } from "@/pages/DatasetsPage";
import { LoginPage } from "@/pages/LoginPage";
import { NewAnalysisPage } from "@/pages/NewAnalysisPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ReportDetailPage } from "@/pages/ReportDetailPage";
import { TaskDetailPage } from "@/pages/TaskDetailPage";
import { TaskHistoryPage } from "@/pages/TaskHistoryPage";
import { getSession } from "@/services/session";

export function ProtectedRoute({ children }: PropsWithChildren) {
  const location = useLocation();
  if (!getSession()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
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
