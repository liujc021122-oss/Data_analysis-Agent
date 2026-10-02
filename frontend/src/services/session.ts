import type { DevSession } from "@/types/session";

export const SESSION_STORAGE_KEY = "data-analysis-agent.session.v1";
export const DEFAULT_DEV_USER_ID =
  import.meta.env.VITE_DEV_USER_ID ?? "00000000-0000-0000-0000-000000000001";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isValidUserId(value: string): boolean {
  return UUID_PATTERN.test(value.trim());
}

function isDevSession(value: unknown): value is DevSession {
  if (!value || typeof value !== "object") {
    return false;
  }

  const session = value as Partial<DevSession>;
  return typeof session.userId === "string"
    && isValidUserId(session.userId)
    && typeof session.displayName === "string"
    && session.displayName.trim().length > 0;
}

export function getSession(): DevSession | null {
  const raw = localStorage.getItem(SESSION_STORAGE_KEY);
  if (!raw) {
    return null;
  }

  try {
    const parsed: unknown = JSON.parse(raw);
    return isDevSession(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function saveSession(session: DevSession): void {
  if (!isDevSession(session)) {
    throw new Error("A valid development user ID and display name are required.");
  }

  localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({
    userId: session.userId.trim(),
    displayName: session.displayName.trim(),
  }));
}

export function clearSession(): void {
  localStorage.removeItem(SESSION_STORAGE_KEY);
}
