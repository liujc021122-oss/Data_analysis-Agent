import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiClient } from "@/services/api/client";

describe("ApiClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("includes browser session credentials on API requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ ok: true }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiClient.get<{ ok: boolean }>("/health")).resolves.toEqual({ ok: true });
    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(requestInit.credentials).toBe("include");
  });

  it("accepts an empty 204 response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(apiClient.delete("/datasets/example")).resolves.toBeUndefined();
  });

  it("preserves the backend error contract", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        code: "TASK_NOT_FOUND",
        message: "task is not available",
        details: { reason: "missing" },
        request_id: "req-1",
      }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    )));

    await expect(apiClient.get("/analysis-tasks/missing")).rejects.toMatchObject({
      code: "TASK_NOT_FOUND",
      message: "task is not available",
      details: { reason: "missing" },
      requestId: "req-1",
      status: 404,
    });
  });

  it("normalizes network failures into an ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const error = await apiClient.get("/datasets").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", status: 0 });
  });
});
