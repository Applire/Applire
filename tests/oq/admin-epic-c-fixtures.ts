// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Mocked Epic C admin API (docs/dev/api-contract-admin.md) for the OQ specs of
 * Administration → Übersicht / Einstellungen / Nutzung / Protokoll and the
 * dashboard instance signal. Synthetic people only (example.org). Every route a
 * page calls is stubbed — an un-stubbed /api/* would proxy to BACKEND_URL.
 */

import type { Page, Route } from "@playwright/test";

export const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const now = Date.now();
export const ago = (minutes: number) => new Date(now - minutes * 60_000).toISOString();

const ZERO = { calls: 0, failed_calls: 0, estimated_calls: 0, prompt_tokens: 0, completion_tokens: 0, reasoning_tokens: 0, total_tokens: 0 };
const tot = (total: number, calls: number, failed = 0, estimated = 0) => ({
  calls,
  failed_calls: failed,
  estimated_calls: estimated,
  prompt_tokens: Math.round(total * 0.78),
  completion_tokens: total - Math.round(total * 0.78),
  reasoning_tokens: 0,
  total_tokens: total,
});

export const PEOPLE = {
  anna: { id: "00000000-0000-0000-0000-000000000001", email: "anna.bauer@example.org" },
  jonas: { id: "00000000-0000-0000-0000-0000000000a2", email: "jonas.keller@example.org" },
  mira: { id: "00000000-0000-0000-0000-0000000000a3", email: "mira.santos@example.org" },
};

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

export function settingsBody(o: { provider?: "requesty" | "openrouter"; panel?: boolean; retention?: boolean; linkedin?: boolean; openrouterKey?: boolean } = {}) {
  const active = o.provider ?? "requesty";
  const choices = ["mistral", "openrouter", "requesty", "anthropic", "openai", "ollama"];
  return {
    items: [
      item("LLM_PROVIDER", { kind: "enum", choices, value: active, source: o.panel ? "panel" : "env", env_value: "requesty", updated_at: o.panel ? ago(5) : null, updated_by_user_id: o.panel ? PEOPLE.anna.id : null, updated_by_email: o.panel ? PEOPLE.anna.email : null }),
      item("REQUESTY_MODEL", { provider: "requesty", value: "openai/gpt-5.6-luna", env_value: "openai/gpt-5.6-luna" }),
      item("REQUESTY_API_KEY", { provider: "requesty", kind: "secret" }),
      item("OPENROUTER_MODEL", { provider: "openrouter", value: "openai/gpt-5.6-luna", source: "default" }),
      item("OPENROUTER_API_KEY", { provider: "openrouter", kind: "secret", is_set: !!o.openrouterKey, env_is_set: false, source: o.openrouterKey ? "panel" : "default", updated_at: o.openrouterKey ? ago(5) : null }),
      item("MISTRAL_MODEL", { provider: "mistral", value: "mistral-small-latest", source: "default" }),
      item("MISTRAL_API_KEY", { provider: "mistral", kind: "secret", is_set: false, env_is_set: false, source: "default" }),
      item("SCRAPER_FETCH_LINKEDIN_GUEST_PAGES", { group: "scraper", kind: "bool", value: o.linkedin ?? true, source: o.linkedin === false ? "panel" : "default" }),
      item("RETENTION_ENABLED", { group: "retention", kind: "bool", value: o.retention ?? true, source: o.retention === false ? "panel" : "default", updated_at: o.retention === false ? ago(30) : null, updated_by_email: o.retention === false ? PEOPLE.anna.email : null }),
    ],
    providers: [
      { id: "mistral", model: "mistral-small-latest", key_required: true, has_key: false, ready: false, active: active === ("mistral" as string), qualification: "qualified", qualification_as_of: "2026-09-16" },
      { id: "openrouter", model: "openai/gpt-5.6-luna", key_required: true, has_key: !!o.openrouterKey, ready: !!o.openrouterKey, active: active === "openrouter", qualification: "qualified", qualification_as_of: "2026-09-16" },
      { id: "requesty", model: "openai/gpt-5.6-luna", key_required: true, has_key: true, ready: true, active: active === "requesty", qualification: "qualified", qualification_as_of: "2026-09-16" },
      { id: "anthropic", model: "claude-sonnet-4-6", key_required: true, has_key: false, ready: false, active: false, qualification: "unmeasured" },
      { id: "openai", model: "gpt-4o", key_required: false, has_key: false, ready: true, active: false, qualification: "unmeasured" },
      { id: "ollama", model: "llama3.2", key_required: false, has_key: false, ready: true, active: false, qualification: "unmeasured" },
    ],
    dependencies: [{ code: "ocr_needs_mistral_key", satisfied: false, keys: ["OCR_BACKEND", "MISTRAL_API_KEY"] }],
  };
}

export function dashboardBody(o: { status?: "ok" | "degraded" | "down"; retention?: boolean } = {}) {
  const status = o.status ?? "degraded";
  return {
    health: {
      status,
      version: "0.44.0-beta",
      edition: "community",
      topology: "prod",
      debug_log_on: false,
      llm_provider: "requesty",
      llm_model: "openai/gpt-5.6-luna",
      checked_at: ago(2),
      components: [
        { name: "database", status: "ok" },
        { name: "migrations", status: "ok" },
        { name: "retention", status: status === "ok" ? "ok" : "degraded" },
        { name: "disk", status: "ok" },
        { name: "backup", status: status === "ok" ? "ok" : "degraded" },
        { name: "provider", status: status === "down" ? "down" : "ok" },
        { name: "errors", status: "ok" },
      ],
    },
    users: { total: 4, active: 3, pending: 1, disabled: 0, admins: 1 },
    usage_30d: tot(740_000, 350, 7, 12),
    failed_jobs: {
      window_days: 7,
      count: 3,
      items: [
        { kind: "import", id: "10000000-0000-0000-0000-000000000001", user_id: PEOPLE.jonas.id, user_email: PEOPLE.jonas.email, failed_at: ago(140), error_code: "llm_timeout" },
        { kind: "gap", id: "10000000-0000-0000-0000-000000000002", user_id: PEOPLE.jonas.id, user_email: PEOPLE.jonas.email, failed_at: ago(60 * 26), error_code: "llm_timeout" },
        { kind: "cover_letter", id: "10000000-0000-0000-0000-000000000003", user_id: PEOPLE.mira.id, user_email: PEOPLE.mira.email, failed_at: ago(60 * 50), error_code: "llm_truncated" },
      ],
    },
    upgrade_notice: null,
    retention: {
      enabled: o.retention ?? true,
      source: o.retention === false ? "panel" : "default",
      last_run_at: ago(60 * 31),
      last_run_ok: true,
      last_run_skipped: o.retention === false,
      enabled_since: o.retention === false ? null : "2026-10-03T08:11:00Z",
      changed_by_email: o.retention === false ? PEOPLE.anna.email : null,
      ttl_days: { uploads: 7, interview_sessions: 30, generated_documents: 90, cancelled_applications: 7, profile_inactivity: 730, audit_log: 730 },
    },
    notices: [],
  };
}

export function usageBody(days = 30) {
  return {
    window_days: days,
    since: ago(days * 1440),
    totals: tot(740_000, 350, 7, 12),
    users: [
      { user_id: PEOPLE.anna.id, email: PEOPLE.anna.email, role: "admin", status: "active", totals: tot(412_300, 188, 2), last_call_at: ago(25) },
      { user_id: PEOPLE.jonas.id, email: PEOPLE.jonas.email, role: "user", status: "active", totals: tot(268_900, 131, 5, 12), last_call_at: ago(140) },
      { user_id: PEOPLE.mira.id, email: PEOPLE.mira.email, role: "user", status: "active", totals: tot(41_200, 22), last_call_at: ago(60 * 50) },
      { user_id: "00000000-0000-0000-0000-0000000000a4", email: "lukas.brandt@example.org", role: "user", status: "pending", totals: ZERO, last_call_at: null },
    ],
    unattributed: tot(17_600, 9),
    by_provider: [
      { provider: "requesty", model: "openai/gpt-5.6-luna", totals: tot(452_000, 214) },
      { provider: "openrouter", model: "openai/gpt-5.6-luna", totals: tot(288_000, 136) },
    ],
    by_document_kind: { cv: tot(355_000, 120), cover_letter: tot(162_800, 70), other: tot(222_200, 160) },
  };
}

const ev = (id: string, minutes: number, action: string, actor: { id: string; email: string } | null, target: { id: string; email: string | null } | null, detail: Record<string, unknown> = {}) => ({
  id: `20000000-0000-0000-0000-0000000000${id}`,
  at: ago(minutes),
  action,
  actor_user_id: actor?.id ?? null,
  actor_email: actor?.email ?? null,
  target_user_id: target?.id ?? null,
  target_email: target?.email ?? null,
  detail,
});

export const AUDIT_PAGE_1 = [
  ev("01", 13, "user.role_changed", PEOPLE.anna, PEOPLE.jonas, { from_role: "user", to_role: "admin" }),
  ev("02", 165, "settings.changed", PEOPLE.anna, null, { key: "LLM_PROVIDER", secret: false, from_source: "env", to_source: "panel", from_value: "openrouter", to_value: "requesty" }),
  ev("03", 166, "settings.changed", PEOPLE.anna, null, { key: "REQUESTY_API_KEY", secret: true, from_source: "env", to_source: "panel" }),
  ev("04", 60 * 19, "invite.redeemed", PEOPLE.mira, PEOPLE.mira, { link_id: "x" }),
  ev("05", 60 * 19 + 24, "user.created", PEOPLE.anna, PEOPLE.mira, { role: "user", mailed: false }),
  ev("06", 60 * 38, "token.created", PEOPLE.jonas, PEOPLE.jonas, { token_id: "t", scope: "agent" }),
  ev("07", 60 * 52, "user.deleted", PEOPLE.anna, { id: "00000000-0000-0000-0000-0000000000a5", email: null }, { by: "admin", erased_rows: 120 }),
];
export const AUDIT_PAGE_2 = [ev("08", 60 * 24 * 4, "setup.claimed", null, PEOPLE.anna, { via: "web" })];
export const AUDIT_ACTIONS = ["setup.claimed", "user.created", "user.role_changed", "user.deleted", "invite.redeemed", "token.created", "settings.changed", "settings.reset", "settings.env_observed", "retention.skipped"];

export interface EpicCStubOptions {
  status?: "ok" | "degraded" | "down";
  retention?: boolean;
  notices?: { code: string; severity: "warning" | "critical" }[];
  uiLanguage?: "de" | "en";
}

/** Stub every route the Epic C pages and the dashboard read. Returns the request log. */
export async function stubEpicC(page: Page, o: EpicCStubOptions = {}) {
  const log: { method: string; url: string; body: string | null }[] = [];
  let settings = settingsBody({ retention: o.retention });
  await page.route("**/api/**", (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    log.push({ method: req.method(), url: path + url.search, body: req.postData() });
    if (path === "/api/admin/dashboard") return json(route, dashboardBody({ status: o.status, retention: o.retention }));
    if (path === "/api/admin/notices") {
      const items = o.notices ?? [];
      return json(route, { count: items.length, items });
    }
    if (path === "/api/admin/settings" && req.method() === "GET") return json(route, settings);
    if (path === "/api/admin/settings" && req.method() === "PUT") {
      const changes = (JSON.parse(req.postData() ?? "{}") as { changes: Record<string, unknown> }).changes;
      if (changes.LLM_PROVIDER === "openrouter" && !changes.OPENROUTER_API_KEY) {
        return json(route, { detail: { error_code: "provider_not_ready", provider: "openrouter", message: "x" } }, 409);
      }
      settings = settingsBody({
        provider: (changes.LLM_PROVIDER as "openrouter" | undefined) ?? "requesty",
        panel: "LLM_PROVIDER" in changes,
        openrouterKey: "OPENROUTER_API_KEY" in changes,
        retention: "RETENTION_ENABLED" in changes ? (changes.RETENTION_ENABLED as boolean) : o.retention,
        linkedin: "SCRAPER_FETCH_LINKEDIN_GUEST_PAGES" in changes ? (changes.SCRAPER_FETCH_LINKEDIN_GUEST_PAGES as boolean) : undefined,
      });
      return json(route, settings);
    }
    if (path.startsWith("/api/admin/settings/") && req.method() === "DELETE") {
      settings = settingsBody({ retention: o.retention });
      return json(route, settings);
    }
    if (path === "/api/admin/usage") return json(route, usageBody(Number(url.searchParams.get("days") ?? 30)));
    if (path === "/api/admin/audit") {
      const cursor = url.searchParams.get("cursor");
      const actions = url.searchParams.getAll("action");
      const limit = Number(url.searchParams.get("limit") ?? 50);
      let items = cursor ? AUDIT_PAGE_2 : AUDIT_PAGE_1;
      if (actions.length) items = [...AUDIT_PAGE_1, ...AUDIT_PAGE_2].filter((e) => actions.includes(e.action));
      items = items.slice(0, limit);
      return json(route, { items, next_cursor: !cursor && !actions.length && limit >= 7 ? "c2-page-2" : null, actions: AUDIT_ACTIONS });
    }
    if (path === "/api/admin/users") {
      return json(route, {
        users: Object.values(PEOPLE).map((p, i) => ({
          id: p.id,
          email: p.email,
          role: i === 0 ? "admin" : "user",
          status: "active",
          created_at: ago(9000),
          last_login_at: ago(10),
          last_active_at: ago(10),
          invite_expires_at: null,
          metadata: { application_count: 3, document_count: 2, storage_bytes: 12_000_000, ai_tokens_30d: 42_000 },
        })),
      });
    }
    if (path === "/api/ops/health") return json(route, { status: "ok", edition: "community", version: "0.44.0-beta", llm_provider: "requesty", checked_at: ago(1), components: {}, usage: {} });
    if (path === "/api/settings") return json(route, { ui_language: o.uiLanguage ?? "en", target_cv_pages: null, default_color_profile_id: null, default_accent_hex: null, hide_predownload_notice: true });
    if (path.startsWith("/api/auth/")) return route.fallback();
    if (path === "/api/admin/color-schemes/active") return json(route, null);
    // The dashboard and the shell read more; an empty answer keeps them quiet.
    return json(route, path.endsWith("s") ? [] : {});
  });
  return log;
}
