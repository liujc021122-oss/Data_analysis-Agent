import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { clearSession, saveSession } from "@/services/session";
import { ProtectedRoute } from "@/app/router";

const userId = "00000000-0000-0000-0000-000000000001";

describe("ProtectedRoute", () => {
  beforeEach(() => {
    clearSession();
  });

  it("redirects unauthenticated visitors to login", () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/datasets"]}>
        <Routes>
          <Route path="/login" element={<p>登录页</p>} />
          <Route
            path="/datasets"
            element={<ProtectedRoute><p>数据集页</p></ProtectedRoute>}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("登录页")).toBeInTheDocument();
    expect(screen.queryByText("数据集页")).not.toBeInTheDocument();
  });

  it("renders protected content for a valid development session", () => {
    saveSession({ userId, displayName: "分析员" });

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/datasets"]}>
        <Routes>
          <Route path="/login" element={<p>登录页</p>} />
          <Route
            path="/datasets"
            element={<ProtectedRoute><p>数据集页</p></ProtectedRoute>}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("数据集页")).toBeInTheDocument();
  });
});
