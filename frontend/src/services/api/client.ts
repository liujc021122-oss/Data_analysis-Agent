import type { ApiErrorResponse } from "@/types/api";

const DEFAULT_TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;
  readonly requestId?: string;

  constructor(
    code: string,
    message: string,
    status: number,
    details: Record<string, unknown> = {},
    requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
    this.requestId = requestId;
  }
}

function apiUrl(path: string): string {
  const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(/\/$/, "");
  return `${baseUrl}/${path.replace(/^\//, "")}`;
}

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (!value || typeof value !== "object") {
    return false;
  }

  const payload = value as Partial<ApiErrorResponse>;
  return typeof payload.code === "string" && typeof payload.message === "string";
}

async function parseResponse(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return undefined;
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json") || contentType.includes("+json")) {
    try {
      return JSON.parse(text) as unknown;
    } catch {
      return undefined;
    }
  }

  return text;
}

export class ApiClient {
  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
    const requestHeaders = new Headers(init.headers);

    requestHeaders.set("Accept", requestHeaders.get("Accept") ?? "application/json");

    if (init.body && !(init.body instanceof FormData) && !requestHeaders.has("Content-Type")) {
      requestHeaders.set("Content-Type", "application/json");
    }

    try {
      const response = await fetch(apiUrl(path), {
        ...init,
        credentials: "include",
        headers: Object.fromEntries(requestHeaders.entries()),
        signal: init.signal ?? controller.signal,
      });
      const payload = await parseResponse(response);

      if (!response.ok) {
        if (isApiErrorResponse(payload)) {
          throw new ApiError(
            payload.code,
            payload.message,
            response.status,
            payload.details ?? {},
            payload.request_id,
          );
        }

        throw new ApiError(
          `HTTP_${response.status}`,
          "请求无法完成，请稍后重试。",
          response.status,
        );
      }

      return payload as T;
    } catch (error) {
      if (error instanceof ApiError) {
        throw error;
      }
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new ApiError("REQUEST_TIMEOUT", "请求超时，请稍后重试。", 0);
      }
      throw new ApiError("NETWORK_ERROR", "无法连接 API，请确认后端服务已启动。", 0);
    } finally {
      window.clearTimeout(timeout);
    }
  }

  get<T>(path: string, init?: RequestInit): Promise<T> {
    return this.request<T>(path, { ...init, method: "GET" });
  }

  post<T>(path: string, body?: unknown, init: RequestInit = {}): Promise<T> {
    return this.request<T>(path, {
      ...init,
      method: "POST",
      body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
    });
  }

  delete<T = void>(path: string, init?: RequestInit): Promise<T> {
    return this.request<T>(path, { ...init, method: "DELETE" });
  }
}

export const apiClient = new ApiClient();
