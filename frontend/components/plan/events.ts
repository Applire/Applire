// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Window events of the tester plans. `REFUSED`: the global fetch patch saw a
 * refusal of the allowance (409 allowance_exhausted | plan_required |
 * instance_daily_limit, 503 allowance_unavailable). `OFFER`: the person pressed
 * "analyse" — offer the plan choice when there is no plan yet. `CHANGED`: a plan
 * change or a purchase happened — every `useAllowance` re-reads.
 * The refused action is never retried on its own; the person presses it again.
 */

import { API_BASE as ALLOWANCE_API_BASE } from "@/lib/auth/api";
import { isApiUrl } from "@/lib/auth/fetch-patch";

export const ALLOWANCE_REFUSED_EVENT = "applire:allowance-refused";
export const ALLOWANCE_CHANGED_EVENT = "applire:allowance-changed";
export const PLAN_OFFER_EVENT = "applire:plan-offer";

export const ALLOWANCE_409_CODES = ["allowance_exhausted", "plan_required", "instance_daily_limit"] as const;
export type AllowanceRefusalCode = (typeof ALLOWANCE_409_CODES)[number] | "allowance_unavailable";

export interface AllowanceRefusalDetail {
  code: AllowanceRefusalCode;
  action: "application" | "profile_import" | null;
  renews_on: string | null;
  /** On the instance-wide refusals: true on the hosted test system (its wording), false on a self-hosted instance. */
  purchasing_available?: boolean | null;
}

export interface AllowanceChangedDetail {
  change?: string;
}

function isRefusalCode(status: number, code: string | null): code is AllowanceRefusalCode {
  if (status === 409) return (ALLOWANCE_409_CODES as readonly (string | null)[]).includes(code);
  if (status === 503) return code === "allowance_unavailable";
  return false;
}

/**
 * Reads ONE clone, taken synchronously before the first `await` (the caller
 * still gets its response unread and may read it at once — a second clone
 * after an await would fail then and drop `action`/`renews_on`, adv/plans F8),
 * and raises one event.
 */
export async function handleAllowanceRefusal(res: Response, url: string): Promise<boolean> {
  if ((res.status !== 409 && res.status !== 503) || !isApiUrl(url)) return false;
  let body: { detail?: { error_code?: unknown; action?: unknown; renews_on?: unknown; purchasing_available?: unknown } } | null =
    null;
  try {
    const copy = res.clone();
    body = await copy.json();
  } catch {
    return false; /* no JSON body: not an allowance refusal */
  }
  const raw = body?.detail?.error_code;
  const code = typeof raw === "string" ? raw : null;
  if (!isRefusalCode(res.status, code)) return false;
  const d = body?.detail;
  const action: AllowanceRefusalDetail["action"] =
    d?.action === "application" || d?.action === "profile_import" ? d.action : null;
  const renews_on = typeof d?.renews_on === "string" ? d.renews_on : null;
  const detail: AllowanceRefusalDetail = { code, action, renews_on };
  if (typeof d?.purchasing_available === "boolean") detail.purchasing_available = d.purchasing_available;
  window.dispatchEvent(new CustomEvent(ALLOWANCE_REFUSED_EVENT, { detail }));
  return true;
}

/**
 * True when `res` is an allowance refusal: the shell's dialog explains it, so
 * the caller adds no failure line of its own (adv/plans F9). Reads a clone.
 */
export async function isAllowanceRefusal(res: Response): Promise<boolean> {
  if (res.status !== 409 && res.status !== 503) return false;
  try {
    const code = (await res.clone().json())?.detail?.error_code;
    return isRefusalCode(res.status, typeof code === "string" ? code : null);
  } catch {
    return false;
  }
}

/** "Stellenanzeige analysieren" was pressed: offer the plan choice if there is no plan yet. */
export function offerPlanChoice(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(PLAN_OFFER_EVENT));
}

export function announceAllowanceChanged(change?: string): void {
  window.dispatchEvent(new CustomEvent<AllowanceChangedDetail>(ALLOWANCE_CHANGED_EVENT, { detail: { change } }));
}

/**
 * DH item 5 (Sorbet invite-gate delivery run, 2026-11-25): before a CV of this job is
 * started from the web, ask the allowance whether the application door would refuse
 * it (`GET /api/me/allowance?job_id=…` → `application_refusal`). When it would, the
 * refusal dialog opens NOW and the caller does not put the posting-terms tick list to
 * the person first. A failed read answers false: the door still decides.
 */
export async function refuseApplicationBeforeTicks(jobId: string | null | undefined): Promise<boolean> {
  if (!jobId || typeof window === "undefined") return false;
  let refusal: { error_code?: unknown; action?: unknown; renews_on?: unknown } | null = null;
  try {
    const res = await fetch(`${ALLOWANCE_API_BASE}/api/me/allowance?job_id=${encodeURIComponent(jobId)}`, {
      method: "GET",
      credentials: "same-origin",
    });
    if (!res.ok) return false;
    refusal = (await res.json())?.application_refusal ?? null;
  } catch {
    return false;
  }
  const code = typeof refusal?.error_code === "string" ? refusal.error_code : null;
  if (code !== "allowance_exhausted" && code !== "plan_required") return false;
  const detail: AllowanceRefusalDetail = {
    code,
    action: refusal?.action === "application" ? "application" : null,
    renews_on: typeof refusal?.renews_on === "string" ? refusal.renews_on : null,
  };
  window.dispatchEvent(new CustomEvent(ALLOWANCE_REFUSED_EVENT, { detail }));
  return true;
}
