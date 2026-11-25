"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/** Plan-ordering helpers: the catalog is read, never hard-coded. */

import type { Allowance, CatalogPlan } from "./api";

export function plansAscending(data: Allowance): CatalogPlan[] {
  return [...(data.catalog?.plans ?? [])].sort((a, b) => a.applications - b.applications);
}

/** The plan just above / below the current one (null = none). */
export function neighbours(data: Allowance): { up: CatalogPlan | null; down: CatalogPlan | null } {
  const plans = plansAscending(data);
  const i = plans.findIndex((p) => p.id === data.plan?.id);
  if (i < 0) return { up: null, down: null };
  return { up: plans[i + 1] ?? null, down: plans[i - 1] ?? null };
}
