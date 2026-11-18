// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Consent to content logs (ADR-091 amended 2026-11-18). Exists only while the
 * instance writes content logs (`LOG_LEVEL=DEBUG`); at any other level the
 * backend answers `required: false` and none of this UI renders.
 *
 * `LOG_CONSENT_REQUIRED_EVENT`: the global fetch patch saw a `409
 * log_consent_required` on an `/api/` call — the shell shows the dialog.
 */

import { API_BASE } from "@/lib/auth/api";

export const LOG_CONSENT_REQUIRED_EVENT = "applire:log-consent-required";

export type LogConsentStateName = "not_required" | "missing" | "outdated" | "withdrawn" | "current";

/** The values the consent text names; "" = not set by the operator. */
export interface LogConsentText {
  version: string;
  text_version: string;
  text_date: string;
  operator: string;
  contact: string;
  assistant_provider: string;
  log_host: string;
  model_provider: string;
  retention_days: number;
}

export interface LogConsentState {
  required: boolean;
  state: LogConsentStateName;
  version?: string | null;
  consented_at?: string | null;
  consented_version?: string | null;
  withdrawn_at?: string | null;
  text?: LogConsentText | null;
}

/** A person who must answer before the shell renders (B2). */
export function needsConsent(state: LogConsentState | null | undefined): boolean {
  return !!state?.required && (state.state === "missing" || state.state === "outdated");
}

/** ICU params for the `logConsent.*` strings; an unset value becomes the catalog's fallback. */
export function consentParams(text: LogConsentText, notSet: string) {
  const or = (v: string) => (v && v.trim() ? v : notSet);
  return {
    operator: or(text.operator),
    contact: or(text.contact),
    assistantProvider: or(text.assistant_provider),
    logHost: or(text.log_host),
    modelProvider: or(text.model_provider),
    days: text.retention_days,
  };
}

const tag = (locale: string) => (locale === "de" ? "de-DE" : "en-GB");

/** "17. November 2026" / "17 November 2026" (board B3). */
export function longDate(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(tag(locale), { day: "numeric", month: "long", year: "numeric" }).format(new Date(iso));
}

/** "17.11.2026" / "17 Nov 2026" (the text's footer). */
export function footerDate(isoDay: string, locale: string): string {
  const d = new Date(`${isoDay}T12:00:00Z`);
  return locale === "de"
    ? new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" }).format(d)
    : new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(d);
}

async function call(method: "GET" | "POST" | "DELETE", body?: unknown): Promise<Response> {
  return fetch(`${API_BASE}/api/me/log-consent`, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export async function readLogConsent(): Promise<LogConsentState | null> {
  try {
    const res = await call("GET");
    return res.ok ? ((await res.json()) as LogConsentState) : null;
  } catch {
    return null;
  }
}

export type ConsentResult = { ok: true; state: LogConsentState } | { ok: false; stale: boolean };

export async function agreeLogConsent(version: string): Promise<ConsentResult> {
  try {
    const res = await call("POST", { version });
    if (res.ok) return { ok: true, state: (await res.json()) as LogConsentState };
    let code: unknown = null;
    try {
      code = (await res.json())?.detail?.error_code;
    } catch {
      /* not JSON */
    }
    return { ok: false, stale: code === "log_consent_version_stale" };
  } catch {
    return { ok: false, stale: false };
  }
}

export async function withdrawLogConsent(): Promise<ConsentResult> {
  try {
    const res = await call("DELETE");
    return res.ok ? { ok: true, state: (await res.json()) as LogConsentState } : { ok: false, stale: false };
  } catch {
    return { ok: false, stale: false };
  }
}
