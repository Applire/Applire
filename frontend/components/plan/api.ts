// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Simulated tester plans and purchases (Sorbet WP-K) — the wire types and the
 * calls. Functions return the `Response` or a parsed body; they never throw on an
 * HTTP status, so the dialogs map `error_code` to copy.
 */

import { API_BASE } from "@/lib/auth";

export interface AllowanceCounter {
  limit: number;
  used: number;
  remaining: number;
  purchased_remaining: number;
}

export interface Allowance {
  policy: "none" | "allowance";
  /** THE capability field: false on every Community install. */
  purchasing_available: boolean;
  renews_on: string;
  applications: (AllowanceCounter & { has_plan: boolean }) | null;
  imports: AllowanceCounter | null;
  plan: { id: string; label: string; applications: number; price_cents: number; currency: string } | null;
  pending: { plan_id: string; label: string; applications: number; from: string } | null;
  catalog: {
    currency: string;
    simulated: boolean;
    plans: CatalogPlan[];
    packs: CatalogPack[];
  } | null;
}

export interface CatalogPlan {
  id: string;
  label: string;
  applications: number;
  price_cents: number;
}

export interface CatalogPack {
  id: string;
  applications: number;
  price_cents: number;
}

export type PlanChange = "chosen" | "upgraded" | "downgrade_scheduled" | "downgrade_cancelled" | "unchanged";

export async function getAllowance(): Promise<Allowance | null> {
  try {
    const res = await fetch(`${API_BASE}/api/me/allowance`, { method: "GET", credentials: "same-origin" });
    return res.ok ? ((await res.json()) as Allowance) : null;
  } catch {
    return null;
  }
}

export type PlanResult = { ok: true; change: PlanChange; allowance: Allowance } | { ok: false };

export async function putPlan(planId: string): Promise<PlanResult> {
  try {
    const res = await fetch(`${API_BASE}/api/me/plan`, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan_id: planId }),
    });
    if (!res.ok) return { ok: false };
    const body = (await res.json()) as { change: PlanChange; allowance: Allowance };
    return { ok: true, change: body.change, allowance: body.allowance };
  } catch {
    return { ok: false };
  }
}

export async function postPurchase(packId: string, idempotencyKey: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/api/me/purchases`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pack_id: packId, idempotency_key: idempotencyKey }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

/** One key per dialog opening: a double click or a retry cannot buy twice. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `k-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
