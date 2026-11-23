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

import { isApiUrl } from "@/lib/auth/fetch-patch";
import { readErrorCode } from "@/lib/auth/api";

export const ALLOWANCE_REFUSED_EVENT = "applire:allowance-refused";
export const ALLOWANCE_CHANGED_EVENT = "applire:allowance-changed";
export const PLAN_OFFER_EVENT = "applire:plan-offer";

export const ALLOWANCE_409_CODES = ["allowance_exhausted", "plan_required", "instance_daily_limit"] as const;
export type AllowanceRefusalCode = (typeof ALLOWANCE_409_CODES)[number] | "allowance_unavailable";

export interface AllowanceRefusalDetail {
  code: AllowanceRefusalCode;
  action: "application" | "profile_import" | null;
  renews_on: string | null;
}

export interface AllowanceChangedDetail {
  change?: string;
}

function isRefusalCode(status: number, code: string | null): code is AllowanceRefusalCode {
  if (status === 409) return (ALLOWANCE_409_CODES as readonly (string | null)[]).includes(code);
  if (status === 503) return code === "allowance_unavailable";
  return false;
}

/** Reads a CLONE (the caller still gets its response unread) and raises one event. */
export async function handleAllowanceRefusal(res: Response, url: string): Promise<boolean> {
  if ((res.status !== 409 && res.status !== 503) || !isApiUrl(url)) return false;
  const code = await readErrorCode(res);
  if (!isRefusalCode(res.status, code)) return false;
  let action: AllowanceRefusalDetail["action"] = null;
  let renews_on: string | null = null;
  try {
    const detail = (await res.clone().json())?.detail;
    if (detail?.action === "application" || detail?.action === "profile_import") action = detail.action;
    if (typeof detail?.renews_on === "string") renews_on = detail.renews_on;
  } catch {
    /* no body details — the dialog falls back to the allowance state */
  }
  const detail: AllowanceRefusalDetail = { code, action, renews_on };
  window.dispatchEvent(new CustomEvent(ALLOWANCE_REFUSED_EVENT, { detail }));
  return true;
}

/** "Stellenanzeige analysieren" was pressed: offer the plan choice if there is no plan yet. */
export function offerPlanChoice(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(PLAN_OFFER_EVENT));
}

export function announceAllowanceChanged(change?: string): void {
  window.dispatchEvent(new CustomEvent<AllowanceChangedDetail>(ALLOWANCE_CHANGED_EVENT, { detail: { change } }));
}
