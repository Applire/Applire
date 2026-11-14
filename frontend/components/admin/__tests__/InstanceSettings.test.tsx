// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { withIntl } from "@/lib/test-utils/with-intl";
import en from "@/messages/en.json";
import { InstanceSettings } from "../settings/InstanceSettings";
import { MODEL_GUIDE_URL } from "../settings/ProviderCard";

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

const KEY_MODE = { mode: "user", secret_set: true, users_without_payer: 3, users_with_grant: 1, users_with_key: 2 };

function settings(over: { keyMode?: Record<string, unknown>; provider?: string; source?: string; retention?: boolean; openrouterKey?: boolean; probe?: string; probeSource?: string } = {}) {
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
      item("LLM_KEY_MODE", { kind: "enum", choices: ["instance", "user"], value: "user", source: "panel", env_value: "instance" }),
      item("OPS_PROVIDER_PROBE", { group: "ops", kind: "enum", choices: ["off", "reachability", "credit", "both"], value: over.probe ?? "both", source: over.probeSource ?? "default", env_value: "both" }),
    ],
    providers: [
      { id: "requesty", model: "openai/gpt-5.6-luna", key_required: true, has_key: true, ready: true, active: active === "requesty", balance_check: false },
      { id: "openrouter", model: "openai/gpt-5.6-luna", key_required: true, has_key: over.openrouterKey ?? false, ready: over.openrouterKey ?? false, active: active === "openrouter", balance_check: true },
    ],
    dependencies: [{ code: "ocr_needs_mistral_key", satisfied: false, keys: ["OCR_BACKEND", "MISTRAL_API_KEY"] }],
    key_mode: { ...KEY_MODE, ...(over.keyMode ?? {}) },
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
  it("shows the source badge and the model-guide link, no per-model verdict, and never a key value", async () => {
    route({});
    render(withIntl(<InstanceSettings />));
    await screen.findByTestId("admin-settings-provider");
    expect(screen.getByTestId("admin-settings-provider-source")).toHaveAttribute("data-source", "env");
    // ADR-093 amended 2026-10-10: the panel links the model guide and judges no model.
    expect(screen.getByTestId("admin-settings-model-guide").querySelector("a")).toHaveAttribute("href", MODEL_GUIDE_URL);
    expect(screen.queryByTestId("admin-settings-qualification")).toBeNull();
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

describe("Einstellungen → Verbindungsprüfung zum Anbieter (ADR-093 amended 2026-11-06)", () => {
  it("offers the four modes, marks the effective one, and writes a choice as one PUT", async () => {
    const puts: Record<string, unknown>[] = [];
    route({ put: (b) => (puts.push(b), res(200, settings({ probe: "off", probeSource: "panel" }))) });
    render(withIntl(<InstanceSettings />));
    const card = await screen.findByTestId("admin-settings-probe");
    for (const mode of ["both", "credit", "reachability", "off"]) {
      expect(within(card).getByTestId(`admin-settings-probe-${mode}`)).toBeInTheDocument();
    }
    expect(within(card).getByRole("radio", { name: new RegExp(T.probeBoth) })).toBeChecked();
    expect(within(card).queryByTestId("admin-settings-probe-off-warning")).toBeNull();
    await userEvent.click(within(card).getByRole("radio", { name: new RegExp(`^${T.probeOff}`) }));
    await waitFor(() => expect(puts).toEqual([{ changes: { OPS_PROVIDER_PROBE: "off" } }]));
    expect(await screen.findByTestId("admin-settings-probe-off-warning")).toHaveTextContent(T.probeOffWarning);
    expect(screen.getByTestId("admin-settings-probe-source")).toHaveAttribute("data-source", "panel");
  });

  it("says when the credit check reads nothing for the active provider", async () => {
    route({});
    render(withIntl(<InstanceSettings />));
    const note = await screen.findByTestId("admin-settings-probe-no-balance");
    expect(note).toHaveTextContent(T.probeCreditNoBalance.replace("{provider}", "requesty"));
  });

  it("names no gap when the active provider has a balance reader", async () => {
    route({ settings: settings({ provider: "openrouter", openrouterKey: true }) });
    render(withIntl(<InstanceSettings />));
    await screen.findByTestId("admin-settings-probe");
    expect(screen.queryByTestId("admin-settings-probe-no-balance")).toBeNull();
  });

  it("the .env line shows the radio's own label for a known mode (789:267g)", async () => {
    route({ settings: settings({ probe: "off", probeSource: "panel" }) });
    render(withIntl(<InstanceSettings />, "de"));
    expect(await screen.findByTestId("admin-settings-probe-env")).toHaveTextContent("In der .env steht: Erreichbarkeit und Guthaben");
  });

  it("an unknown .env mode stays raw", async () => {
    const base = settings();
    const withRaw = { ...base, items: base.items.map((i: { key: string }) => (i.key === "OPS_PROVIDER_PROBE" ? { ...i, env_value: "weird" } : i)) };
    route({ settings: withRaw });
    render(withIntl(<InstanceSettings />, "de"));
    expect(await screen.findByTestId("admin-settings-probe-env")).toHaveTextContent("In der .env steht: weird");
  });

  it("reset to .env sends DELETE for the probe key", async () => {
    const deleted: string[] = [];
    route({ settings: settings({ probe: "off", probeSource: "panel" }), del: (k) => (deleted.push(k), res(200, settings())) });
    render(withIntl(<InstanceSettings />));
    await userEvent.click(await screen.findByTestId("admin-settings-probe-reset"));
    await waitFor(() => expect(deleted).toEqual(["OPS_PROVIDER_PROBE"]));
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

describe("Einstellungen → Automatisches Löschen: a skipped worker run (adv-admin ADM-1)", () => {
  function routeDash(retention: Record<string, unknown>) {
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === "/api/admin/dashboard") return res(200, { ...DASH, retention: { ...DASH.retention, ...retention } });
      if (url === "/api/admin/settings" && method === "GET") return res(200, settings());
      return res(404, {});
    });
  }

  it("names the skipped run even while the panel reads on", async () => {
    routeDash({ last_run_skipped: true, last_run_at: "2026-10-04T03:00:00Z", enabled_since: "2026-10-05T03:00:00Z" });
    render(withIntl(<InstanceSettings />));
    const line = await screen.findByTestId("admin-settings-retention-last-run-skipped");
    expect(line).toHaveTextContent(T.retentionLastRunSkipped.split("{date}")[0].trim());
  });

  it("shows nothing extra when the last run did not skip", async () => {
    routeDash({ last_run_skipped: false, last_run_at: "2026-10-04T03:00:00Z" });
    render(withIntl(<InstanceSettings />));
    await screen.findByTestId("admin-settings-retention-on");
    expect(screen.queryByTestId("admin-settings-retention-last-run-skipped")).toBeNull();
  });
});

describe("Einstellungen → Automatisches Löschen: what the switch governs (E2E-2, ruling C1-3)", () => {
  const TTL = { uploads: 7, interview_sessions: 30, generated_documents: 90, cancelled_applications: 7, profile_inactivity: 730, audit_log: 730 };
  function routeTtl(retention: boolean) {
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === "/api/admin/dashboard") return res(200, { ...DASH, retention: { ...DASH.retention, enabled: retention, ttl_days: TTL } });
      if (url === "/api/admin/settings" && method === "GET") return res(200, settings({ retention }));
      return res(404, {});
    });
  }
  const cancelled = T.ttlCancelled.replace("{days}", "7");
  const audit = T.ttlAudit.replace("{days}", "730");

  it("lists the rules C1-3 never suspends apart from the switch, in both states", async () => {
    for (const on of [true, false]) {
      routeTtl(on);
      render(withIntl(<InstanceSettings />));
      const always = await screen.findByTestId("admin-settings-retention-always");
      expect(always).toHaveTextContent(cancelled);
      expect(always).toHaveTextContent(audit);
      expect(always).toHaveTextContent(T.alwaysAuthLinks);
      const state = screen.getByTestId(on ? "admin-settings-retention-on" : "admin-settings-retention-off");
      expect(state).not.toHaveTextContent(cancelled);
      expect(state).not.toHaveTextContent(audit);
      cleanup();
    }
  });

  it("the OFF copy never claims nothing is deleted", () => {
    expect(T.retentionOffIntro).not.toMatch(/no longer deletes anything/);
    expect(T.retentionOffBody).not.toMatch(/no longer deletes anything/);
  });
});


describe("Einstellungen → Wer bezahlt die KI-Aufrufe? (ADR-096)", () => {
  it("shows the mode, the people-without-payer line and no secret warning when the secret is set", async () => {
    route({});
    render(withIntl(<InstanceSettings />));
    await screen.findByTestId("admin-settings-keymode");
    expect(screen.getByTestId("admin-settings-keymode-user").querySelector("input")).toBeChecked();
    expect(screen.getByTestId("admin-settings-keymode-without")).toHaveTextContent("3 people have neither a key nor a grant and will see the notice.");
    expect(screen.queryByTestId("admin-settings-keymode-secret-missing")).toBeNull();
  });

  it("warns when PROVIDER_KEY_SECRET is unset, and maps a 409 provider_key_secret_missing to that sentence", async () => {
    route({
      settings: settings({ keyMode: { mode: "instance", secret_set: false, users_without_payer: 1 } }),
      put: () => res(409, { detail: { error_code: "provider_key_secret_missing", message: "raw server text" } }),
    });
    const u = userEvent.setup();
    render(withIntl(<InstanceSettings />));
    await screen.findByTestId("admin-settings-keymode");
    expect(screen.getByTestId("admin-settings-keymode-secret-missing")).toHaveTextContent("PROVIDER_KEY_SECRET is not set");
    await u.click(screen.getByTestId("admin-settings-keymode-instance").querySelector("input")!);
    await u.click(screen.getByTestId("admin-settings-keymode-user").querySelector("input")!);
    const err = await screen.findByTestId("admin-settings-keymode-error");
    expect(err).toHaveTextContent(T.keyModeSecretMissing);
    expect(err).not.toHaveTextContent("raw server text");
  });
});
