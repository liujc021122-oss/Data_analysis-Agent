import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { AppLayout } from "@/components/layout/AppLayout";
import { clearSession, getSession, saveSession } from "@/services/session";

const userId = "00000000-0000-0000-0000-000000000001";

describe("AppLayout", () => {
  beforeEach(() => {
    clearSession();
    saveSession({ userId, displayName: "分析员" });
  });

  it("renders primary navigation with keyboard-focusable links", () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/datasets"]}>
        <Routes>
          <Route path="/datasets" element={<AppLayout><main>内容</main></AppLayout>} />
        </Routes>
      </MemoryRouter>,
    );

    const datasetsLink = screen.getByRole("link", { name: "数据集" });
    expect(datasetsLink).toHaveAttribute("href", "/datasets");
    datasetsLink.focus();
    expect(datasetsLink).toHaveFocus();
    expect(screen.getByRole("link", { name: "新建分析" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "历史任务" })).toBeInTheDocument();
  });

  it("clears the local session and navigates to login", () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/datasets"]}>
        <Routes>
          <Route path="/datasets" element={<AppLayout><main>内容</main></AppLayout>} />
          <Route path="/login" element={<p>登录页</p>} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

    expect(getSession()).toBeNull();
    expect(screen.getByText("登录页")).toBeInTheDocument();
  });
});
