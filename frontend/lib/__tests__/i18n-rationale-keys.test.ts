// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

import { describe, expect, it } from "vitest";
import de from "@/messages/de.json";
import en from "@/messages/en.json";

/**
 * ADR-038: a `FieldChange` carries a stable `rationale_key`; the "what changed
 * & why" surface renders `review.rationale.<key>` in the user's UI language and
 * falls back to the backend's English prose when the key is unknown
 * (`components/review/WhatChangedReview.tsx`). The fallback keeps the surface
 * working, but it is English prose in a German UI — so every key the backend
 * actually emits must resolve in BOTH catalogs.
 *
 * Keep this list in sync with the backend:
 *   grep -rn 'rationale_key' backend/applire/
 * (note: the three `manual_section_*` keys come from a lookup table in
 * `reconcile/apply.py`, not a `rationale_key="…"` literal)
 *
 * The reverse assertion (no catalog key without an emitter) is deliberately
 * NOT made: the namespace still carries entries from earlier merge surfaces
 * that no current writer produces.
 */
const BACKEND_RATIONALE_KEYS = [
  // backend/applire/services/profile/reconcile/apply.py
  "confirmation_resolved",
  "conflict_resolved",
  "denial_probe_asked",
  "gap_marked_na",
  "manual_section_added",
  "manual_section_removed",
  "manual_section_updated",
  "reconcile_added",
  "reconcile_merged",
  "reconcile_summary",
  "reconcile_updated",
  "role_added",
  "role_closed",
  // backend/applire/services/profile/__init__.py
  "profile_updated",
];

type Catalog = Record<string, unknown>;

function rationaleNamespace(catalog: Catalog): Record<string, unknown> {
  const review = catalog["review"] as Record<string, unknown>;
  return (review?.["rationale"] ?? {}) as Record<string, unknown>;
}

const deRationale = rationaleNamespace(de as Catalog);
const enRationale = rationaleNamespace(en as Catalog);

describe("review.rationale catalog coverage (ADR-038)", () => {
  it("every backend-emitted rationale_key resolves in BOTH catalogs", () => {
    for (const key of BACKEND_RATIONALE_KEYS) {
      expect(typeof deRationale[key], `de.json review.rationale.${key}`).toBe("string");
      expect(typeof enRationale[key], `en.json review.rationale.${key}`).toBe("string");
    }
  });

  it("carries no ICU placeholders — the renderer resolves these keys with no values", () => {
    for (const [key, value] of [
      ...Object.entries(deRationale),
      ...Object.entries(enRationale),
    ]) {
      expect(String(value), `review.rationale.${key} must not interpolate`).not.toMatch(/[{}]/);
    }
  });
});
