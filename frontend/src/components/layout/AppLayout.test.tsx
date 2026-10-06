import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/app/auth";
import { NotificationProvider } from "@/app/notifications";
import { AppLayout } from "@/components/layout/AppLayout";
import type { AuthUser } from "@/types/auth";

const user: AuthUser = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "auth-user@example.test",
  role: "ADMIN",
  is_active: true,
  created_at: "2026-10-06T00:00:00Z",
};

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function apiErrorResponse(): Response {
  return new Response(JSON.stringify({
    code: "LOGOUT_FAILED",
    message: "退出登录失败，请稍后重试。",
    details: { internal: "backend error body" },
    request_id: "req-logout-1",
  }), {
    status: 503,
    headers: { "Content-Type": "application/json" },
  });
}

function renderLayoutWithAuth() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <NotificationProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/datasets"]}>
            <Routes>
              <Route path="/datasets" element={<AppLayout><main>内容</main></AppLayout>} />
              <Route path="/login" element={<p>登录页</p>} />
            </Routes>
          </MemoryRouter>
        </AuthProvider>
      </QueryClientProvider>
    </NotificationProvider>,
  );
}

describe("AppLayout", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders primary navigation with keyboard-focusable links", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(user)));
    renderLayoutWithAuth();
    await screen.findByText(user.email);

    const datasetsLink = screen.getByRole("link", { name: "数据集" });
    expect(datasetsLink).toHaveAttribute("href", "/datasets");
    datasetsLink.focus();
    expect(datasetsLink).toHaveFocus();
    expect(screen.getByRole("link", { name: "新建分析" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "历史任务" })).toBeInTheDocument();
  });

  it("renders the authenticated email and role", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(user)));
    renderLayoutWithAuth();

    expect(await screen.findByText(user.email)).toBeInTheDocument();
    expect(screen.getByText("管理员")).toBeInTheDocument();
  });

  it("calls the backend logout endpoint and navigates to login", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(user))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    renderLayoutWithAuth();

    await screen.findByText(user.email);
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

    expect(await screen.findByText("登录页")).toBeInTheDocument();
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/auth/logout");
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).credentials).toBe("include");
  });

  it("redirects after a failed logout and reports the API error safely", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(user))
      .mockResolvedValueOnce(apiErrorResponse());
    vi.stubGlobal("fetch", fetchMock);
    renderLayoutWithAuth();

    fireEvent.click(await screen.findByRole("button", { name: "退出登录" }));

    expect(await screen.findByText("登录页")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("退出登录失败，请稍后重试。");
    expect(screen.getByRole("alert")).toHaveTextContent("请求编号：req-logout-1");
    expect(screen.getByRole("alert")).not.toHaveTextContent("backend error body");
  });

});
