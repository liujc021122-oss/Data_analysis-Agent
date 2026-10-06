import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/app/auth";
import { NotificationProvider } from "@/app/notifications";
import { LoginPage } from "@/pages/LoginPage";
import { DatasetsPage } from "@/pages/DatasetsPage";
import { NewAnalysisPage } from "@/pages/NewAnalysisPage";
import { TaskDetailPage } from "@/pages/TaskDetailPage";
import { TaskHistoryPage } from "@/pages/TaskHistoryPage";

const user = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "auth-user@example.test",
  role: "ADMIN" as const,
  is_active: true,
  created_at: "2026-10-06T00:00:00Z",
};

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function apiErrorResponse(code: string, status: number): Response {
  return new Response(JSON.stringify({ code, message: code }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderWithQuery(ui: ReactNode, initialEntries: string[] = ["/"]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <NotificationProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={initialEntries} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          {ui}
        </MemoryRouter>
      </QueryClientProvider>
    </NotificationProvider>,
  );
}

describe("frontend pages", () => {
  beforeEach(() => undefined);

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("logs in with email and password and returns to the protected page", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(apiErrorResponse("AUTHENTICATION_REQUIRED", 401))
      .mockResolvedValueOnce(jsonResponse(user));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider>
          <MemoryRouter initialEntries={[{ pathname: "/login", search: "?from=tasks", state: { from: "/datasets" } }]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/datasets" element={<p>受保护工作区</p>} />
            </Routes>
          </MemoryRouter>
        </AuthProvider>
      </QueryClientProvider>,
    );

    await screen.findByLabelText("邮箱");
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "test-password-1234" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("受保护工作区")).toBeInTheDocument();
    expect(localStorage.length).toBe(0);
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ credentials: "include", method: "POST" });
  });

  it("shows a stable error for invalid credentials", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(apiErrorResponse("AUTHENTICATION_REQUIRED", 401))
      .mockResolvedValueOnce(apiErrorResponse("INVALID_CREDENTIALS", 401));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider>
          <MemoryRouter initialEntries={["/login"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
            <LoginPage />
          </MemoryRouter>
        </AuthProvider>
      </QueryClientProvider>,
    );
    fireEvent.change(await screen.findByLabelText("邮箱"), { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("邮箱或密码错误");
  });

  it("shows an empty state when the dataset list is empty", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ items: [], page: 1, page_size: 20, total: 0, has_next: false }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));
    renderWithQuery(<DatasetsPage />);

    expect(await screen.findByText("还没有数据集")).toBeInTheDocument();
    expect(screen.getByText("上传一个 CSV 文件开始分析。")).toBeInTheDocument();
  });

  it("shows an empty state when task history has no items", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ items: [], page: 1, page_size: 20, total: 0, has_next: false }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));
    renderWithQuery(<TaskHistoryPage />);

    expect(await screen.findByText("还没有历史任务")).toBeInTheDocument();
  });

  it("validates the new analysis form before submission", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        items: [{
          dataset_id: "dataset-1",
          name: "sales.csv",
          content_type: "text/csv",
          size_bytes: 12,
          checksum: null,
          created_at: "2026-09-28T00:00:00Z",
          profile: { encoding: "utf-8", delimiter: ",", row_count: 1, column_count: 1, columns: [], preview_rows: [], sensitive_fields: [] },
        }],
        page: 1,
        page_size: 100,
        total: 1,
        has_next: false,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));
    renderWithQuery(<NewAnalysisPage />);

    await screen.findByLabelText("选择 sales.csv");
    fireEvent.change(screen.getByLabelText("分析需求"), { target: { value: "分析销售趋势" } });
    fireEvent.click(screen.getByRole("button", { name: "创建分析任务" }));

    expect(screen.getByRole("alert")).toHaveTextContent("请选择至少一个数据集");
  });

  it("shows a task error with a retry action", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ code: "TASK_NOT_FOUND", message: "task is not available", details: {}, request_id: "req-1" }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    renderWithQuery(<TaskDetailPage />, ["/tasks/task-1"]);

    expect(await screen.findByText("任务暂时不可用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2));
  });
});
