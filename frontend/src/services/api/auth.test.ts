import { afterEach, describe, expect, it, vi } from "vitest";
import { getCurrentUser, login, logout } from "@/services/api/auth";

const user = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "13634930829@163.com",
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

describe("auth API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads the current user", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(user));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getCurrentUser()).resolves.toEqual(user);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/auth/me");
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).credentials).toBe("include");
  });

  it("logs in with email and password", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(user));
    vi.stubGlobal("fetch", fetchMock);

    await expect(login({ email: user.email, password: "test-password-1234" })).resolves.toEqual(user);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ email: user.email, password: "test-password-1234" });
  });

  it("logs out with an empty 204 response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(logout()).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/auth/logout");
  });
});
