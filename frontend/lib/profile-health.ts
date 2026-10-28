// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// Hazelnut build 5 (UAT-3d; ADR-099 cl. 2, 10, 11) — what the profile health
// hub may show, and its one-action resolvers that are plain API calls.
//
// The backend classifies every HealthIssue (`delivery_class` + `resolver`). The
// hub renders class A and U; class S (e.g. `accuracy`, "Datenabgleich") stays in
// the API for agents and is never rendered. An UNCLASSIFIED issue (`null`/absent
// class — an older backend, or a kind not registered yet) renders exactly as
// before: "None means unclassified — never render it as S".

import type { HealthIssue, ProfileHealth } from "@/components/profile/HealthPanel";
import type { ProfileSectionsResponse } from "@/lib/profile-entries";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export function isRenderedIssue(issue: Pick<HealthIssue, "delivery_class">): boolean {
  return issue.delivery_class !== "S";
}

export function renderedIssues(health: ProfileHealth | null | undefined): HealthIssue[] {
  return (health?.issues ?? []).filter(isRenderedIssue);
}

/**
 * Completeness without `field_gaps` is class S (ADR-099 cl. 11): the
 * "Fehlende Abschnitte" label had no action unless the interview had a question
 * to ask, so the hub shows the completeness line only together with its one
 * action ("Verbessern").
 */
export function completenessActionable(health: ProfileHealth | null | undefined): boolean {
  const block = health?.completeness;
  if (block?.delivery_class) return block.delivery_class !== "S";
  return (block?.field_gaps ?? []).length > 0;
}

/** The hub card is worth mounting: a rendered issue or an actionable gap. */
export function hubHasContent(health: ProfileHealth | null | undefined): boolean {
  return renderedIssues(health).length > 0 || completenessActionable(health);
}

export const BUDGET_UNITS = ["EUR", "USD", "CHF", "GBP"] as const;
export type BudgetUnit = (typeof BUDGET_UNITS)[number];

// `postJson`: the route tripwire reads this helper's calls as POST.
async function postJson(path: string, body: unknown): Promise<ProfileSectionsResponse | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return null;
    return (await res.json()) as ProfileSectionsResponse;
  } catch {
    return null;
  }
}

/** UAT-3d — "Übernehmen" for ONE not-applied item, through the reconcile door. */
export function adoptNotApplied(issueId: string, itemKey: string) {
  return postJson("/api/profile/not-applied/adopt", { issue_id: issueId, item_key: itemKey });
}

/** UAT-3d — the unit for a budget figure, accepted in place. */
export function saveBudgetUnit(entryId: string, unit: BudgetUnit) {
  return postJson("/api/profile/health/unit", { entry_id: entryId, unit });
}

/** U-7 (ruling B-1, INTERFACE A1-3) — the longest outcome the endpoint takes. */
export const ASPIRATION_OUTCOME_MAX = 500;

/**
 * U-7 — "Ist das erreicht?". `achieved` + `outcome` appends the outcome to the
 * entry's achievements; `achieved: false` keeps the bullet as a goal. A 409
 * `already_answered` counts as done (the issue is gone on reload).
 */
export async function answerAspiration(
  issueId: string,
  achieved: boolean,
  outcome?: string,
): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/api/profile/aspirations/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        achieved ? { issue_id: issueId, achieved, outcome } : { issue_id: issueId, achieved },
      ),
    });
    return res.ok || res.status === 409;
  } catch {
    return false;
  }
}
