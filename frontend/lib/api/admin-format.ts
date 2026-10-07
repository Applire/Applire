// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Pure mappers from the Epic C admin payloads to catalog keys + ICU params.
 * Params are locale-neutral (codes, ids, numbers, raw values): every German or
 * English word comes from the catalog, never from the backend (applire-i18n:
 * "a backend-built param carries an English phrase" trap).
 */

import type { FailedJobKind } from "./admin";

export interface MsgRef {
  /** Key inside the namespace the caller translates with. */
  key: string;
  params?: Record<string, string | number>;
}

/** `adminDashboard.*` key for a failed job's kind. */
export function failedKindKey(kind: FailedJobKind | string): string {
  switch (kind) {
    case "import":
      return "kindImport";
    case "gap":
      return "kindGap";
    case "cv":
      return "kindCv";
    case "cover_letter":
      return "kindLetter";
    default:
      return "kindImport";
  }
}

/**
 * `adminDashboard.*` reason for a stable job error code (classify_gap_error /
 * classify_import_error / classify_generation_error). Unknown codes are shown
 * verbatim inside `reasonOther` — a code, never message text.
 */
export function failedReason(code: string | null | undefined): MsgRef {
  switch (code) {
    case "llm_timeout":
      return { key: "reasonTimeout" };
    case "llm_truncated":
      return { key: "reasonTruncated" };
    case "rate_limited":
      return { key: "reasonRateLimited" };
    default:
      return { key: "reasonOther", params: { code: code || "unknown" } };
  }
}

/** Audit actions the catalog names (`adminAudit.action.<action with . → _>`). */
export const KNOWN_AUDIT_ACTIONS = [
  "setup.claimed",
  "harness.boot",
  "password.changed",
  "oidc.linked",
  "oidc.unlinked",
  "token.created",
  "token.revoked",
  "tokens.revoked_all",
  "user.created",
  "user.reinvited",
  "invite.redeemed",
  "user.role_changed",
  "user.disabled",
  "user.enabled",
  "user.deleted",
  "reset_link.issued",
  "password.reset",
  "settings.changed",
  "settings.reset",
  "settings.env_observed",
  "retention.skipped",
] as const;

/** `adminAudit.*` label of an audit row. A secret change gets its own label (no values). */
export function auditActionLabel(action: string, detail: Record<string, unknown>): MsgRef {
  if (action === "settings.changed" && detail.secret === true) return { key: "action.settings_secretChanged" };
  if ((KNOWN_AUDIT_ACTIONS as readonly string[]).includes(action)) {
    return { key: `action.${action.replace(/\./g, "_")}` };
  }
  return { key: "action.unknown", params: { action } };
}

function scalar(v: unknown): string {
  if (v === true) return "true";
  if (v === false) return "false";
  if (v === null || v === undefined) return "";
  return String(v);
}

/**
 * The detail line of an audit row as catalog refs (`adminAudit.detail.*`).
 * Ids (link_id, token_id, erased_rows) are deliberately not shown — they mean
 * nothing to a reader. A secret's values are never in `detail` (contract §3.2);
 * this function would not show them even if they were.
 */
export function auditDetailParts(action: string, detail: Record<string, unknown>): MsgRef[] {
  const out: MsgRef[] = [];
  if (action === "user.role_changed" && detail.from_role && detail.to_role) {
    out.push({ key: "detail.roleChange", params: { from: scalar(detail.from_role), to: scalar(detail.to_role) } });
  } else if (typeof detail.role === "string") {
    out.push({ key: "detail.role", params: { value: detail.role } });
  }
  if (action.startsWith("settings.") && typeof detail.key === "string") {
    if (detail.secret === true) {
      out.push({ key: "detail.secret", params: { setting: detail.key } });
    } else if ("to_value" in detail || "from_value" in detail) {
      out.push({
        key: "detail.setting",
        params: { setting: detail.key, from: scalar(detail.from_value) || "—", to: scalar(detail.to_value) || "—" },
      });
    } else {
      out.push({ key: "detail.secret", params: { setting: detail.key } });
    }
  }
  if (typeof detail.mailed === "boolean") out.push({ key: detail.mailed ? "detail.mailedYes" : "detail.mailedNo" });
  if (typeof detail.scope === "string") out.push({ key: "detail.scope", params: { value: detail.scope } });
  if (typeof detail.count === "number") out.push({ key: "detail.count", params: { count: detail.count } });
  if (detail.by === "admin") out.push({ key: "detail.byAdmin" });
  if (detail.by === "self") out.push({ key: "detail.bySelf" });
  if (detail.via === "web") out.push({ key: "detail.viaWeb" });
  if (detail.via === "cli") out.push({ key: "detail.viaCli" });
  return out;
}

/**
 * Role/bool raw values inside an audit detail param, mapped to a FULLY
 * QUALIFIED catalog key (roles reuse the approved `shell.role*` words).
 */
export function auditValueKey(value: string): string | null {
  switch (value) {
    case "admin":
      return "shell.roleAdmin";
    case "user":
      return "shell.roleUser";
    case "true":
      return "adminAudit.detail.on";
    case "false":
      return "adminAudit.detail.off";
    default:
      return null;
  }
}
