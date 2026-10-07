// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { withIntl } from "@/lib/test-utils/with-intl";
import en from "@/messages/en.json";
import { InstanceSettings } from "../settings/InstanceSettings";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/admin/settings" }));

const T = en.adminSettings;
const mockFetch = vi.fn();

const item = (key: string, over: Record<string, unknown> = {}) => ({
  key,
  group: "llm",
  kind: "string",
  provider: null,
  choices: null,
  value: null,
  source: "env",
  env_value: null,
  is_set: true,
  env_is_set: true,
  updated_at: null,
  updated_by_user_id: null,
  ...over,
});

function settings(over: { provider?: string; source?: string; retention?: boolean; openrouterKey?: boolean } = {}) {
  const active = over.provider ?? "requesty";
  return {
    items: [
      item("LLM_PROVIDER", { kind: "enum", choices: ["requesty", "openrouter"], value: active, source: over.source ?? "env", env_value: "requesty" }),
      item("REQUESTY_MODEL", { provider: "requesty", value: "openai/gpt-5.6-luna" }),
      item("REQUESTY_API_KEY", { provider: "requesty", kind: "secret", value: null, is_set: true }),
      item("OPENROUTER_MODEL", { provider: "openrouter", value: "openai/gpt-5.6-luna", source: "default" }),
      item("OPENROUTER_API_KEY", { provider: "openrouter", kind: "secret", value: null, is_set: over.openrouterKey ?? false, env_is_set: false, source: "default" }),
      item("SCRAPER_FETCH_LINKEDIN_GUEST_PAGES", { group: "scraper", kind: "bool", value: true, source: "default" }),
      item("RETENTION_ENABLED", { group: "retention", kind: "bool", value: over.retention ?? true, source: "default" }),
    ],
    providers: [
      { id: "requesty", model: "openai/gpt-5.6-luna", key_required: true, has_key: true, ready: true, active: active === "requesty", qualification: "qualified", qualification_as_of: "2026-09-16" },
      { id: "openrouter", model: "openai/gpt-5.6-luna", key_required: true, has_key: over.openrouterKey ?? false, ready: over.openrouterKey ?? false, active: active === "openrouter" },
    ],
    dependencies: [{ code: "ocr_needs_mistral_key", satisfied: false, keys: ["OCR_BACKEND", "MISTRAL_API_KEY"] }],
  };
}

const DASH = {
  health: { status: "ok", version: "0.44.0", edition: "community", topology: "prod", debug_log_on: false, llm_provider: "requesty", llm_model: "x", checked_at: null, components: [] },
  users: { total: 3, active: 3, pending: 0, disabled: 0, admins: 1 },
  usage_30d: { calls: 0, failed_calls: 0, estimated_calls: 0, prompt_tokens: 0, completion_tokens: 0, reasoning_tokens: 0, total_tokens: 0 },
  failed_jobs: { window_days: 7, count: 0, items: [] },
  upgrade_notice: null,
  retention: { enabled: true, source: "default", last_run_at: null, last_run_ok: null, last_run_skipped: null },
  notices: [],
};

const res = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function route(handlers: { put?: (body: Record<string, unknown>) => Response; settings?: unknown; del?: (key: string) => Response }) {
  mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url === "/api/admin/dashboard") return res(200, DASH);
    if (url === "/api/admin/settings" && method === "GET") return res(200, handlers.settings ?? settings());
    if (url === "/api/admin/settings" && method === "PUT") return handlers.put!(JSON.parse(String(init!.body)));
    if (url.startsWith("/api/admin/settings/") && method === "DELETE") return handlers.del!(url.split("/").pop()!);
    return res(404, {});
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
  mockFetch.mockReset();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Einstellungen → KI-Anbieter (#710)", () => {
  it("shows the source badge and the qualification, and never a key value", async () => {
    route({});
    render(withIntl(<InstanceSettings />));
    await screen.findByTestId("admin-settings-provider");
    expect(screen.getByTestId("admin-settings-provider-source")).toHaveAttribute("data-source", "env");
    expect(screen.getByTestId("admin-settings-qualification")).toHaveAttribute("data-qualification", "qualified");
    expect(screen.getByTestId("admin-settings-key-state")).toHaveTextContent(T.keyStoredEnv);
    expect(screen.queryByTestId("admin-settings-key-input")).toBeNull(); // write-only: no field until "replace"
    expect(screen.getByTestId("admin-settings-dep-ocr")).toHaveAttribute("data-satisfied", "false");
  });

  it("switching provider + key is ONE atomic PUT after confirmation, and the typed key is cleared", async () => {
    const puts: Record<string, unknown>[] = [];
    route({
      put: (b) => {
        puts.push(b);
        return res(200, settings({ provider: "openrouter", source: "panel", openrouterKey: true }));
      },
    });
    const u = userEvent.setup();
    render(withIntl(<InstanceSettings />));
    await u.selectOptions(await screen.findByTestId("admin-settings-provider-select"), "openrouter");
    await u.type(screen.getByTestId("admin-settings-key-input"), "sk-or-secret");
    await u.click(screen.getByTestId("admin-settings-provider-apply"));
    expect(await screen.findByTestId("admin-settings-switch-dialog")).toBeInTheDocument();
    expect(puts).toHaveLength(0);
    await u.click(screen.getByTestId("admin-settings-switch-confirm"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ changes: { LLM_PROVIDER: "openrouter", OPENROUTER_API_KEY: "sk-or-secret" } });
    await screen.findByTestId("admin-settings-saved");
    expect(document.body.innerHTML).not.toContain("sk-or-secret");
    expect(screen.getByTestId("admin-settings-provider-source")).toHaveAttribute("data-source", "panel");
    expect(screen.getByTestId("admin-settings-provider-env")).toHaveTextContent("requesty");
  });

  it("a provider without a key is refused before any request", async () => {
    route({ put: () => res(500, {}) });
    const u = userEvent.setup();
    render(withIntl(<InstanceSettings />));
    await u.selectOptions(await screen.findByTestId("admin-settings-provider-select"), "openrouter");
    await u.click(screen.getByTestId("admin-settings-provider-apply"));
    expect(screen.getByTestId("admin-settings-provider-error")).toHaveTextContent("openrouter");
    expect(mockFetch.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === "PUT")).toBe(false);
  });

  it("maps 409 provider_not_ready and keeps no typed key after the refusal", async () => {
    route({ put: () => res(409, { detail: { error_code: "provider_not_ready", provider: "openrouter" } }) });
    const u = userEvent.setup();
    render(withIntl(<InstanceSettings />));
    await u.selectOptions(await screen.findByTestId("admin-settings-provider-select"), "openrouter");
    await u.type(screen.getByTestId("admin-settings-key-input"), "sk-bad");
    await u.click(screen.getByTestId("admin-settings-provider-apply"));
    await u.click(await screen.findByTestId("admin-settings-switch-confirm"));
    expect(await screen.findByTestId("admin-settings-provider-error")).toHaveTextContent("openrouter");
    expect((screen.getByTestId("admin-settings-key-input") as HTMLInputElement).value).toBe("");
  });

  it("reset to .env sends DELETE for the key", async () => {
    const deleted: string[] = [];
    route({
      settings: settings({ provider: "openrouter", source: "panel", openrouterKey: true }),
      del: (k) => {
        deleted.push(k);
        return res(200, settings());
      },
    });
    const u = userEvent.setup();
    render(withIntl(<InstanceSettings />));
    await u.click(await screen.findByTestId("admin-settings-provider-reset"));
    await waitFor(() => expect(deleted).toEqual(["LLM_PROVIDER"]));
  });
});

describe("Einstellungen → LinkedIn (#726) und automatisches Löschen (#738)", () => {
  it("the LinkedIn switch writes the boolean immediately", async () => {
    const puts: Record<string, unknown>[] = [];
    route({ put: (b) => (puts.push(b), res(200, settings())) });
    const u = userEvent.setup();
    render(withIntl(<InstanceSettings />));
    await u.click(await screen.findByTestId("admin-settings-linkedin-toggle"));
    await waitFor(() => expect(puts).toEqual([{ changes: { SCRAPER_FETCH_LINKEDIN_GUEST_PAGES: false } }]));
  });

  it("switching deletion OFF asks first; cancelling sends nothing", async () => {
    const puts: Record<string, unknown>[] = [];
    route({ put: (b) => (puts.push(b), res(200, settings({ retention: false }))) });
    const u = userEvent.setup();
    render(withIntl(<InstanceSettings />));
    await u.click(await screen.findByTestId("admin-settings-retention-toggle"));
    const dialog = await screen.findByTestId("admin-settings-retention-dialog");
    expect(dialog).toHaveTextContent(T.retentionOffTitle);
    await u.click(within(dialog).getByText(T.cancel, { selector: "button" }));
    expect(puts).toHaveLength(0);
    await u.click(screen.getByTestId("admin-settings-retention-toggle"));
    await u.click(await screen.findByTestId("admin-settings-retention-confirm"));
    await waitFor(() => expect(puts).toEqual([{ changes: { RETENTION_ENABLED: false } }]));
    expect(await screen.findByTestId("admin-settings-retention-off")).toBeInTheDocument();
    expect(screen.getByTestId("admin-settings-retention-others")).toHaveTextContent("2");
  });

  it("switching deletion ON needs no confirmation", async () => {
    const puts: Record<string, unknown>[] = [];
    route({ settings: settings({ retention: false }), put: (b) => (puts.push(b), res(200, settings())) });
    const u = userEvent.setup();
    render(withIntl(<InstanceSettings />));
    await u.click(await screen.findByTestId("admin-settings-retention-toggle"));
    await waitFor(() => expect(puts).toEqual([{ changes: { RETENTION_ENABLED: true } }]));
    expect(screen.queryByTestId("admin-settings-retention-dialog")).toBeNull();
  });

  it("a 403 renders the 'for admins' state", async () => {
    mockFetch.mockResolvedValue(res(403, {}));
    render(withIntl(<InstanceSettings />));
    expect(await screen.findByTestId("admin-forbidden")).toBeInTheDocument();
  });
});
