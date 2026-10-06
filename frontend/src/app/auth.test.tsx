import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "@/app/auth";
import { ApiError } from "@/services/api/client";
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

function apiErrorResponse(code: string, status: number): Response {
  return new Response(JSON.stringify({ code, message: code }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function AuthProbe() {
  const { status, user: currentUser, bootstrapError, retryBootstrap, login, logout, isLoggingIn, isLoggingOut } = useAuth();

  return (
    <div>
      <p>{status}</p>
      <p>{currentUser?.email}</p>
      <p>{bootstrapError?.code}</p>
      <p>{isLoggingIn ? "logging in" : "not logging in"}</p>
      <p>{isLoggingOut ? "logging out" : "not logging out"}</p>
      <button onClick={retryBootstrap}>retry</button>
      <button onClick={() => void login({ email: user.email, password: "test-password-1234" })}>login</button>
      <button onClick={() => void logout()}>logout</button>
    </div>
  );
}

function renderWithAuth(ui: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{ui}</AuthProvider>
    </QueryClientProvider>,
  );
  return queryClient;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AuthProvider", () => {
  it("restores a valid server session", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(user)));

    renderWithAuth(<AuthProbe />);

    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(await screen.findByText("authenticated")).toBeInTheDocument();
    expect(screen.getByText(user.email)).toBeInTheDocument();
  });

  it("treats a 401 session lookup as unauthenticated without an error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiErrorResponse("AUTHENTICATION_REQUIRED", 401)));

    renderWithAuth(<AuthProbe />);

    expect(await screen.findByText("unauthenticated")).toBeInTheDocument();
    expect(screen.queryByText("error")).not.toBeInTheDocument();
  });

  it("exposes a retry action for session lookup failures", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(apiErrorResponse("NETWORK_ERROR", 503))
      .mockResolvedValueOnce(jsonResponse(user));
    vi.stubGlobal("fetch", fetchMock);

    renderWithAuth(<AuthProbe />);
    expect(await screen.findByText("error")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "retry" }));
    expect(await screen.findByText("authenticated")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("logs in, exposes its loading flag, clears cached queries, and authenticates", async () => {
    let resolveLogin: ((response: Response) => void) | undefined;
    const loginResponse = new Promise<Response>((resolve) => {
      resolveLogin = resolve;
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(apiErrorResponse("AUTHENTICATION_REQUIRED", 401))
      .mockImplementationOnce(() => loginResponse);
    vi.stubGlobal("fetch", fetchMock);

    const queryClient = renderWithAuth(<AuthProbe />);
    await screen.findByText("unauthenticated");
    queryClient.setQueryData(["datasets"], ["stale"]);
    fireEvent.click(screen.getByRole("button", { name: "login" }));

    expect(await screen.findByText("logging in")).toBeInTheDocument();
    resolveLogin?.(jsonResponse(user));
    expect(await screen.findByText("authenticated")).toBeInTheDocument();
    expect(screen.getByText(user.email)).toBeInTheDocument();
    expect(queryClient.getQueryData(["datasets"])).toBeUndefined();
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).credentials).toBe("include");
  });

  it("logs out, exposes its loading flag, clears cached queries, and becomes unauthenticated", async () => {
    let resolveLogout: ((response: Response) => void) | undefined;
    const logoutResponse = new Promise<Response>((resolve) => {
      resolveLogout = resolve;
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(user))
      .mockImplementationOnce(() => logoutResponse);
    vi.stubGlobal("fetch", fetchMock);

    const queryClient = renderWithAuth(<AuthProbe />);
    await screen.findByText("authenticated");
    queryClient.setQueryData(["datasets"], ["stale"]);
    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    expect(await screen.findByText("logging out")).toBeInTheDocument();
    resolveLogout?.(new Response(null, { status: 204 }));
    expect(await screen.findByText("unauthenticated")).toBeInTheDocument();
    expect(queryClient.getQueryData(["datasets"])).toBeUndefined();
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).credentials).toBe("include");
  });

  it("keeps logout state when a pending bootstrap response resolves later", async () => {
    let resolveBootstrap: ((response: Response) => void) | undefined;
    const bootstrapResponse = new Promise<Response>((resolve) => {
      resolveBootstrap = resolve;
    });
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => bootstrapResponse)
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    renderWithAuth(<AuthProbe />);
    fireEvent.click(screen.getByRole("button", { name: "logout" }));
    expect(await screen.findByText("logging out")).toBeInTheDocument();

    expect(await screen.findByText("unauthenticated")).toBeInTheDocument();
    await act(async () => {
      resolveBootstrap?.(jsonResponse({ ...user, email: "stale@example.test" }));
      await bootstrapResponse;
    });
    await waitFor(() => expect(screen.getByText("unauthenticated")).toBeInTheDocument());
    expect(screen.queryByText("stale@example.test")).not.toBeInTheDocument();
  });

  it("preserves the latest session lookup when an older response resolves later", async () => {
    let resolveFirst: ((response: Response) => void) | undefined;
    const firstResponse = new Promise<Response>((resolve) => {
      resolveFirst = resolve;
    });
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => firstResponse)
      .mockResolvedValueOnce(jsonResponse(user));
    vi.stubGlobal("fetch", fetchMock);

    renderWithAuth(<AuthProbe />);
    fireEvent.click(screen.getByRole("button", { name: "retry" }));
    expect(await screen.findByText("authenticated")).toBeInTheDocument();
    resolveFirst?.(jsonResponse({ ...user, email: "stale@example.com" }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText("stale@example.com")).not.toBeInTheDocument();
    expect(screen.getByText(user.email)).toBeInTheDocument();
  });

  it("throws a clear error when useAuth is called outside its provider", () => {
    expect(() => render(<AuthProbe />)).toThrow("useAuth must be used within an AuthProvider");
  });

  it("uses ApiError values for non-authentication bootstrap failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiErrorResponse("NETWORK_ERROR", 503)));

    renderWithAuth(<AuthProbe />);

    expect(await screen.findByText("NETWORK_ERROR")).toBeInTheDocument();
    expect(screen.getByText("error")).toBeInTheDocument();
    expect(new ApiError("NETWORK_ERROR", "failure", 503)).toBeInstanceOf(ApiError);
  });
});
