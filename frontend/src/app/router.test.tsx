import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/app/auth";
import { ProtectedRoute } from "@/app/router";

vi.mock("@/components/layout/AppLayout", () => ({
  AppLayout: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

const user = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "auth-user@example.test",
  role: "ADMIN" as const,
  is_active: true,
  created_at: "2026-10-06T00:00:00Z",
};

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
}

function apiErrorResponse(code: string, status: number): Response {
  return new Response(JSON.stringify({ code, message: code }), { status, headers: { "Content-Type": "application/json" } });
}

function renderProtected(initialEntries: string[], fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock);
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={initialEntries}>
          <Routes>
            <Route path="/login" element={<LoginProbe />} />
            <Route path="*" element={<ProtectedRoute><p>数据集页</p></ProtectedRoute>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

function LoginProbe() {
  const location = useLocation();
  return <p>登录页:{(location.state as { from?: string } | null)?.from}</p>;
}

describe("ProtectedRoute", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state while checking the session", () => {
    renderProtected(["/datasets"], vi.fn().mockReturnValue(new Promise<Response>(() => undefined)));

    expect(screen.getByText("正在验证登录状态")).toBeInTheDocument();
  });

  it("redirects a 401 session lookup to login with the original internal location", async () => {
    renderProtected(["/datasets?tab=recent#summary"], vi.fn().mockResolvedValue(apiErrorResponse("AUTHENTICATION_REQUIRED", 401)));

    expect(await screen.findByText("登录页:/datasets?tab=recent#summary")).toBeInTheDocument();
    expect(screen.queryByText("数据集页")).not.toBeInTheDocument();
  });

  it("renders protected content for an authenticated session", async () => {
    renderProtected(["/datasets"], vi.fn().mockResolvedValue(jsonResponse(user)));

    expect(await screen.findByText("数据集页")).toBeInTheDocument();
  });

  it("shows a retryable error for a non-401 bootstrap failure", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(apiErrorResponse("NETWORK_ERROR", 503))
      .mockResolvedValueOnce(jsonResponse(user));
    renderProtected(["/datasets"], fetchMock);

    expect(await screen.findByText("认证服务暂时不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(screen.queryByText("登录页")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("数据集页")).toBeInTheDocument();
  });
});
