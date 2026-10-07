// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import {
  actionsForCategory,
  auditCategoryOf,
  auditQueryString,
  getAuditPage,
  getDashboard,
  getInstanceSettings,
  getUsage,
  providerKey,
  resetInstanceSetting,
  signalFrom,
  tokenShare,
  updateInstanceSettings,
} from "../admin";
import { useAdminNotices, useAuditLog, useInstanceSettings } from "../admin-hooks";

const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
  mockFetch.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function res(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const SETTINGS = {
  items: [
    { key: "LLM_PROVIDER", group: "llm", kind: "enum", provider: null, choices: ["mistral", "openrouter"], value: "openrouter", source: "panel", env_value: "requesty", is_set: true, env_is_set: true, updated_at: "2026-10-07T09:20:00Z", updated_by_user_id: "u1" },
    { key: "OPENROUTER_API_KEY", group: "llm", kind: "secret", provider: "openrouter", choices: null, value: null, source: "panel", env_value: null, is_set: true, env_is_set: false, updated_at: null, updated_by_user_id: null },
  ],
  providers: [],
  dependencies: [],
};

describe("admin client — result envelope", () => {
  it("maps 403 to forbidden and 401 to unauthenticated, never to a generic error", async () => {
    mockFetch.mockResolvedValueOnce(res(403, { detail: { error_code: "forbidden" } }));
    expect(await getDashboard()).toEqual({ ok: false, kind: "forbidden", status: 403 });
    mockFetch.mockResolvedValueOnce(res(401, { detail: { error_code: "unauthenticated" } }));
    expect(await getDashboard()).toEqual({ ok: false, kind: "unauthenticated", status: 401 });
  });

  it("carries the structured error_code of a refused write (409 provider_not_ready)", async () => {
    mockFetch.mockResolvedValueOnce(res(409, { detail: { error_code: "provider_not_ready", provider: "requesty" } }));
    const r = await updateInstanceSettings({ LLM_PROVIDER: "requesty" });
    expect(r.ok).toBe(false);
    if (!r.ok && r.kind === "error") {
      expect(r.code).toBe("provider_not_ready");
      expect(r.detail?.provider).toBe("requesty");
    } else {
      throw new Error("expected an error result");
    }
  });

  it("reports a thrown fetch as network, not as forbidden", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    expect(await getUsage(7)).toEqual({ ok: false, kind: "network", status: 0 });
  });
});

describe("admin client — requests", () => {
  it("PUTs one atomic changes map with the session cookie", async () => {
    mockFetch.mockResolvedValueOnce(res(200, SETTINGS));
    await updateInstanceSettings({ LLM_PROVIDER: "openrouter", OPENROUTER_API_KEY: "sk-test" });
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/admin/settings");
    expect(init.method).toBe("PUT");
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(init.body)).toEqual({ changes: { LLM_PROVIDER: "openrouter", OPENROUTER_API_KEY: "sk-test" } });
  });

  it("resets an override with DELETE on the encoded key", async () => {
    mockFetch.mockResolvedValueOnce(res(200, SETTINGS));
    await resetInstanceSetting("LLM_PROVIDER");
    expect(mockFetch.mock.calls[0][0]).toBe("/api/admin/settings/LLM_PROVIDER");
    expect(mockFetch.mock.calls[0][1].method).toBe("DELETE");
  });

  it("a secret item has no value to render — the client passes null through untouched", async () => {
    mockFetch.mockResolvedValueOnce(res(200, SETTINGS));
    const r = await getInstanceSettings();
    if (!r.ok) throw new Error("expected ok");
    const secret = r.data.items.find((i) => i.kind === "secret")!;
    expect(secret.value).toBeNull();
    expect(secret.is_set).toBe(true);
  });

  it("asks usage for the chosen window", async () => {
    mockFetch.mockResolvedValueOnce(res(200, {}));
    await getUsage(90);
    expect(mockFetch.mock.calls[0][0]).toBe("/api/admin/usage?days=90");
  });

  it("builds the audit query with repeated action params and the cursor", () => {
    expect(auditQueryString({})).toBe("");
    const q = auditQueryString({ actions: ["user.created", "user.deleted"], actorId: "a1", limit: 50, cursor: "c=" });
    expect(q).toBe("?action=user.created&action=user.deleted&actor_id=a1&limit=50&cursor=c%3D");
  });

  it("GETs the audit page with the query string", async () => {
    mockFetch.mockResolvedValueOnce(res(200, { items: [], next_cursor: null, actions: [] }));
    await getAuditPage({ targetUserId: "t1" });
    expect(mockFetch.mock.calls[0][0]).toBe("/api/admin/audit?target_user_id=t1");
  });
});

describe("admin client — helpers", () => {
  it("derives the per-provider registry keys", () => {
    expect(providerKey("openrouter", "model")).toBe("OPENROUTER_MODEL");
    expect(providerKey("requesty", "key")).toBe("REQUESTY_API_KEY");
  });

  it("puts every known action in a category and filters by the server's list", () => {
    expect(auditCategoryOf("user.role_changed")).toBe("accounts");
    expect(auditCategoryOf("invite.redeemed")).toBe("accounts");
    expect(auditCategoryOf("tokens.revoked_all")).toBe("access");
    expect(auditCategoryOf("reset_link.issued")).toBe("access");
    expect(auditCategoryOf("settings.env_observed")).toBe("settings");
    expect(auditCategoryOf("retention.skipped")).toBe("instance");
    expect(auditCategoryOf("something.new")).toBe("instance");
    const all = ["user.created", "settings.changed", "settings.reset", "token.created"];
    expect(actionsForCategory(all, "settings")).toEqual(["settings.changed", "settings.reset"]);
    expect(actionsForCategory(all, "all")).toEqual([]);
  });

  it("computes a bounded token share", () => {
    expect(tokenShare(0, 0)).toBe(0);
    expect(tokenShare(50, 200)).toBe(25);
    expect(tokenShare(300, 200)).toBe(100);
  });

  it("the dashboard signal is null with no notices and critical when any notice is", () => {
    expect(signalFrom(null)).toBeNull();
    expect(signalFrom({ count: 0, items: [] })).toBeNull();
    expect(signalFrom({ count: 2, items: [{ code: "health_degraded", severity: "warning" }, { code: "failed_jobs", severity: "warning" }] })).toEqual({ count: 2, critical: false });
    expect(signalFrom({ count: 2, items: [{ code: "health_down", severity: "critical" }, { code: "failed_jobs", severity: "warning" }] })).toEqual({ count: 2, critical: true });
  });
});

describe("admin hooks", () => {
  it("a 403 lands in the forbidden state", async () => {
    mockFetch.mockResolvedValueOnce(res(403, {}));
    const { result } = renderHook(() => useInstanceSettings());
    await waitFor(() => expect(result.current.status).toBe("forbidden"));
  });

  it("notices are never requested for a non-admin", async () => {
    const { result } = renderHook(() => useAdminNotices(false));
    await new Promise((r) => setTimeout(r, 10));
    expect(mockFetch).not.toHaveBeenCalled();
    expect(result.current.status).toBe("loading");
  });

  it("the audit log appends the next keyset page and stops at the last", async () => {
    const row = (id: string) => ({ id, at: "2026-10-07T10:00:00Z", action: "user.created", actor_user_id: null, actor_email: null, target_user_id: null, target_email: null, detail: {} });
    mockFetch
      .mockResolvedValueOnce(res(200, { items: [row("1"), row("2")], next_cursor: "k2", actions: ["user.created"] }))
      .mockResolvedValueOnce(res(200, { items: [row("3")], next_cursor: null, actions: ["user.created"] }));
    const { result } = renderHook(() => useAuditLog({ limit: 2 }));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.hasMore).toBe(true);
    await act(async () => {
      await result.current.loadMore();
    });
    expect(result.current.items.map((i) => i.id)).toEqual(["1", "2", "3"]);
    expect(result.current.hasMore).toBe(false);
    expect(mockFetch.mock.calls[1][0]).toBe("/api/admin/audit?limit=2&cursor=k2");
  });
});
