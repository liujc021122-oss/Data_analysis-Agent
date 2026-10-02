import { beforeEach, describe, expect, it } from "vitest";
import { clearSession, getSession, saveSession } from "@/services/session";

const userId = "00000000-0000-0000-0000-000000000001";

describe("development session", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("round-trips the development session", () => {
    saveSession({ userId, displayName: "分析员" });

    expect(getSession()).toEqual({ userId, displayName: "分析员" });
  });

  it("ignores malformed storage and can clear a valid session", () => {
    localStorage.setItem("data-analysis-agent.session.v1", "broken");
    expect(getSession()).toBeNull();

    saveSession({ userId, displayName: "分析员" });
    clearSession();
    expect(getSession()).toBeNull();
  });
});
