import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { saveSession } from "@/services/session";
import { createTask, getArtifact, uploadDataset } from "@/services/api/resources";

const userId = "00000000-0000-0000-0000-000000000001";

describe("API resources", () => {
  beforeEach(() => {
    saveSession({ userId, displayName: "分析员" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the typed task creation request unchanged", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ task_id: "task-1", status: "QUEUED", created: true, enqueued: true }),
      { status: 202, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const request = {
      query: "分析销售趋势",
      idempotency_key: "request-1",
      dataset_ids: ["dataset-1"],
      max_rounds: 10,
      metadata: { source: "frontend" },
    };

    await createTask(request);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual(request);
  });

  it("uses FormData for dataset uploads", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ dataset_id: "dataset-1", profile: { columns: [] } }),
      { status: 201, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["name,value"], "sales.csv", { type: "text/csv" });

    await uploadDataset(file);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBe(file);
  });

  it("returns artifact authorization URLs without rewriting them", async () => {
    const artifact = {
      artifact_id: "artifact-1",
      artifact_type: "report",
      name: "report.html",
      download_url: "local-download://signed-token",
      content_url: "/api/artifacts/artifact-1/content?token=signed-token",
      format: "HTML",
      mime_type: "text/html",
      description: "报告",
      created_at: "2026-09-28T00:00:00Z",
      metadata: {},
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify(artifact),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )));

    await expect(getArtifact("artifact-1")).resolves.toEqual(artifact);
  });
});
