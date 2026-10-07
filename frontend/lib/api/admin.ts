// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Epic C admin REST client (#694 #710 #726 #738, S-11, B2-2) — shapes mirror
 * `backend/applire/schemas/admin.py` section "Epic C" and
 * `docs/dev/api-contract-admin.md` (ADR-093). Kept thin: every call returns an
 * `AdminResult` so a page can tell "forbidden" (non-admin → 403 state) from
 * "unavailable" (network / 5xx) and map an `error_code` to copy.
 *
 * Metadata only (S-4, RD-6, RD-11): nothing here carries vault content, error
 * message text, an IP address or a secret value. A secret setting has
 * `value === null` and is described by `is_set` alone — the client never sends
 * a secret anywhere except in the `PUT` body that sets it.
 */

import { API_BASE } from "@/lib/auth";

// ---------------------------------------------------------------------------
// Result envelope
// ---------------------------------------------------------------------------

export type AdminFailure =
  | { ok: false; kind: "unauthenticated"; status: 401 }
  | { ok: false; kind: "forbidden"; status: 403 }
  | { ok: false; kind: "error"; status: number; code: string | null; detail: Record<string, unknown> | null }
  | { ok: false; kind: "network"; status: 0 };

export type AdminResult<T> = { ok: true; data: T } | AdminFailure;

async function readDetail(res: Response): Promise<Record<string, unknown> | null> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return body && typeof body.detail === "object" && body.detail !== null
      ? (body.detail as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

async function call<T>(path: string, init?: { method?: string; body?: unknown }): Promise<AdminResult<T>> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: init?.method ?? "GET",
      credentials: "same-origin",
      headers: init?.body === undefined ? undefined : { "Content-Type": "application/json" },
      body: init?.body === undefined ? undefined : JSON.stringify(init.body),
    });
  } catch {
    return { ok: false, kind: "network", status: 0 };
  }
  if (res.status === 401) return { ok: false, kind: "unauthenticated", status: 401 };
  if (res.status === 403) return { ok: false, kind: "forbidden", status: 403 };
  if (!res.ok) {
    const detail = await readDetail(res);
    const code = typeof detail?.error_code === "string" ? detail.error_code : null;
    return { ok: false, kind: "error", status: res.status, code, detail };
  }
  try {
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return { ok: false, kind: "error", status: res.status, code: null, detail: null };
  }
}

// ---------------------------------------------------------------------------
// §2 Instance settings
// ---------------------------------------------------------------------------

export type SettingKind = "enum" | "string" | "bool" | "secret";
export type SettingGroup = "llm" | "scraper" | "retention";
export type SettingSource = "panel" | "env" | "default";
export type SettingValue = string | boolean;

export interface InstanceSettingItem {
  key: string;
  group: SettingGroup;
  kind: SettingKind;
  provider: string | null;
  choices: string[] | null;
  /** Effective value; ALWAYS null for a secret. */
  value: SettingValue | null;
  source: SettingSource;
  env_value: SettingValue | null;
  is_set: boolean;
  env_is_set: boolean;
  updated_at: string | null;
  updated_by_user_id: string | null;
  /** Requested additively from C1 (C2 contract request #4) — may be absent. */
  updated_by_email?: string | null;
}

export type ModelQualification = "qualified" | "not_qualified" | "unmeasured";

export interface ProviderStatus {
  id: string;
  model: string;
  key_required: boolean;
  has_key: boolean;
  ready: boolean;
  active: boolean;
  /** Requested additively from C1 (C2 contract request #1) — may be absent. */
  qualification?: ModelQualification;
  qualification_reason?: string | null;
  qualification_as_of?: string | null;
}

export interface SettingDependency {
  code: string;
  satisfied: boolean;
  keys: string[];
}

export interface InstanceSettingsResponse {
  items: InstanceSettingItem[];
  providers: ProviderStatus[];
  dependencies: SettingDependency[];
}

export const SETTING_KEYS = {
  provider: "LLM_PROVIDER",
  linkedin: "SCRAPER_FETCH_LINKEDIN_GUEST_PAGES",
  retention: "RETENTION_ENABLED",
} as const;

/** `<PROVIDER>_MODEL` / `<PROVIDER>_API_KEY` — the per-provider registry keys (contract §2.1). */
export function providerKey(provider: string, field: "model" | "key"): string {
  return `${provider.toUpperCase()}_${field === "model" ? "MODEL" : "API_KEY"}`;
}

export function getInstanceSettings(): Promise<AdminResult<InstanceSettingsResponse>> {
  return call("/api/admin/settings");
}

/** Atomic write (contract §2.5). Switching provider + entering its key = ONE call. */
export function updateInstanceSettings(changes: Record<string, SettingValue>): Promise<AdminResult<InstanceSettingsResponse>> {
  return call("/api/admin/settings", { method: "PUT", body: { changes } });
}

/** Remove a panel override: back to the env / default value. */
export function resetInstanceSetting(key: string): Promise<AdminResult<InstanceSettingsResponse>> {
  return call(`/api/admin/settings/${encodeURIComponent(key)}`, { method: "DELETE" });
}

export function findSetting(res: InstanceSettingsResponse, key: string): InstanceSettingItem | undefined {
  return res.items.find((i) => i.key === key);
}

// ---------------------------------------------------------------------------
// §3 Audit view
// ---------------------------------------------------------------------------

export interface AuditEventItem {
  id: string;
  at: string;
  action: string;
  actor_user_id: string | null;
  actor_email: string | null;
  target_user_id: string | null;
  target_email: string | null;
  detail: Record<string, unknown>;
}

export interface AuditPageResponse {
  items: AuditEventItem[];
  next_cursor: string | null;
  actions: string[];
}

export interface AuditQuery {
  actions?: string[];
  actorId?: string;
  targetUserId?: string;
  since?: string;
  until?: string;
  limit?: number;
  cursor?: string;
}

export function auditQueryString(q: AuditQuery): string {
  const p = new URLSearchParams();
  for (const a of q.actions ?? []) p.append("action", a);
  if (q.actorId) p.set("actor_id", q.actorId);
  if (q.targetUserId) p.set("target_user_id", q.targetUserId);
  if (q.since) p.set("since", q.since);
  if (q.until) p.set("until", q.until);
  if (q.limit !== undefined) p.set("limit", String(q.limit));
  if (q.cursor) p.set("cursor", q.cursor);
  const s = p.toString();
  return s ? `?${s}` : "";
}

export function getAuditPage(q: AuditQuery = {}): Promise<AdminResult<AuditPageResponse>> {
  return call(`/api/admin/audit${auditQueryString(q)}`);
}

/** The filter pills of the audit view — action prefixes per category (mock admin-audit.html). */
export type AuditCategory = "all" | "accounts" | "access" | "settings" | "instance";

const CATEGORY_OF_PREFIX: [string, AuditCategory][] = [
  ["user.", "accounts"],
  ["invite.", "accounts"],
  ["password.", "access"],
  ["reset_link.", "access"],
  ["oidc.", "access"],
  ["token.", "access"],
  ["tokens.", "access"],
  ["settings.", "settings"],
  ["setup.", "instance"],
  ["harness.", "instance"],
  ["retention.", "instance"],
];

export function auditCategoryOf(action: string): AuditCategory {
  return CATEGORY_OF_PREFIX.find(([p]) => action.startsWith(p))?.[1] ?? "instance";
}

/** The exact action names of a category, from the server's `actions` list (never a hard-coded set). */
export function actionsForCategory(all: string[], category: AuditCategory): string[] {
  if (category === "all") return [];
  return all.filter((a) => auditCategoryOf(a) === category);
}

// ---------------------------------------------------------------------------
// §4 Usage
// ---------------------------------------------------------------------------

export interface UsageTotals {
  calls: number;
  failed_calls: number;
  estimated_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
}

export type AdminUserStatus = "pending" | "active" | "disabled";

export interface UsageUserRow {
  user_id: string;
  email: string;
  role: "admin" | "user";
  status: AdminUserStatus;
  totals: UsageTotals;
  last_call_at: string | null;
}

export interface UsageProviderRow {
  provider: string;
  model: string;
  totals: UsageTotals;
}

export interface AdminUsageResponse {
  window_days: number;
  since: string;
  totals: UsageTotals;
  users: UsageUserRow[];
  unattributed: UsageTotals;
  by_provider: UsageProviderRow[];
  /** Requested additively from C1 (C2 contract request #2) — may be absent. */
  by_document_kind?: { cv: UsageTotals; cover_letter: UsageTotals; other: UsageTotals };
}

export const USAGE_PERIODS = [7, 30, 90] as const;
export type UsagePeriod = (typeof USAGE_PERIODS)[number];

export function getUsage(days: UsagePeriod = 30): Promise<AdminResult<AdminUsageResponse>> {
  return call(`/api/admin/usage?days=${days}`);
}

/** Share of the window's tokens in percent (0–100, integer); 0 for an empty window. */
export function tokenShare(part: number, total: number): number {
  if (total <= 0 || part <= 0) return 0;
  return Math.min(100, Math.round((part / total) * 100));
}

// ---------------------------------------------------------------------------
// §5 Dashboard and notices
// ---------------------------------------------------------------------------

export type AdminNoticeCode =
  | "health_down"
  | "health_degraded"
  | "upgrade_notice"
  | "retention_disabled"
  | "failed_jobs"
  | "debug_log_on"
  | "dev_topology"
  | "provider_not_ready"
  | "ocr_needs_mistral_key"
  | "settings_secret_unreadable";

export interface AdminNotice {
  code: AdminNoticeCode | string;
  severity: "warning" | "critical";
}

export interface AdminNoticesResponse {
  count: number;
  items: AdminNotice[];
}

export interface DashboardHealth {
  status: "ok" | "degraded" | "down" | string;
  version: string;
  edition: string;
  topology: string;
  debug_log_on: boolean;
  llm_provider: string;
  llm_model: string;
  checked_at: string | null;
  components: { name: string; status: string }[];
}

export type FailedJobKind = "cv" | "cover_letter" | "import" | "gap";

export interface FailedJobItem {
  kind: FailedJobKind;
  id: string;
  user_id: string | null;
  user_email: string | null;
  failed_at: string | null;
  error_code: string | null;
}

export interface DashboardRetention {
  enabled: boolean;
  source: SettingSource;
  last_run_at: string | null;
  last_run_ok: boolean | null;
  last_run_skipped: boolean | null;
  /** Requested additively from C1 (C2 contract request #3) — may be absent. */
  enabled_since?: string | null;
  changed_by_email?: string | null;
}

export interface AdminDashboardResponse {
  health: DashboardHealth;
  users: { total: number; active: number; pending: number; disabled: number; admins: number };
  usage_30d: UsageTotals;
  failed_jobs: { window_days: number; count: number; items: FailedJobItem[] };
  upgrade_notice: Record<string, unknown> | null;
  retention: DashboardRetention;
  notices: AdminNotice[];
}

export function getDashboard(): Promise<AdminResult<AdminDashboardResponse>> {
  return call("/api/admin/dashboard");
}

export function getNotices(): Promise<AdminResult<AdminNoticesResponse>> {
  return call("/api/admin/notices");
}

/**
 * The one-line dashboard signal (#694): null when there is nothing to say,
 * otherwise the count and whether any notice is critical (red "problem" copy).
 */
export function signalFrom(n: AdminNoticesResponse | null): { count: number; critical: boolean } | null {
  if (!n || n.items.length === 0) return null;
  return { count: n.items.length, critical: n.items.some((i) => i.severity === "critical") };
}
