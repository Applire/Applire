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

import { useTranslations } from "next-intl";
import { enrichmentSourceKey } from "@/lib/enrichment-sources";

/**
 * #604 — the one composition every conflict surface renders through.
 *
 * #626 gave the Health hub's conflict card a heading that names the entry
 * ("Senior Engineer @ Acme — End date") instead of the backend's raw
 * `work_experience.end_date: 'x' vs 'y'`. That composition lived inside
 * `HealthPanel`, so it reached one of the product's two conflict surfaces: the
 * live enrichment interview's own `ConflictCard` is fed by a different
 * mechanism (`schemas/session.py`'s `ConflictSummary`) and kept the raw shape.
 *
 * Both mechanisms now populate the same structured facts (backend:
 * `services/profile/entity_label.py`) and render through the functions here.
 * A third surface imports this module; it does not re-derive the wording.
 *
 * The strings live under the `health` i18n namespace because that is where the
 * convention was defined — a non-Health caller passes `useTranslations("health")`
 * deliberately, exactly as `ProfileReviewDrawer` already did.
 */
export interface ConflictFacts {
  entity_label?: string | null;
  section?: string | null;
  field?: string | null;
  existing_value_display?: string | null;
  incoming_value_display?: string | null;
  // The INCOMING side's provenance only. The existing side's is not
  // recoverable from a `Conflict` — see the backend `HealthIssue` docstring.
  incoming_source?: string | null;
}

export interface ConflictDescription {
  heading: string;
  existingRow: string;
  incomingRow: string;
}

type Translator = ReturnType<typeof useTranslations>;

/**
 * Human field label. `professional_summary` is special-cased: its `field` is a
 * language slot ("de"/"en"), not a real field name (see the backend
 * `_conflict_issue`). Falls back to the raw key (underscores → spaces) for a
 * field outside the dictionary — informative without fabricating a translation.
 */
export function conflictFieldLabel(t: Translator, facts: ConflictFacts): string {
  const field = facts.field ?? "";
  if (facts.section === "professional_summary") {
    return field === "de" ? t("fieldLabel.summaryDe") : t("fieldLabel.summaryEn");
  }
  const key = `fieldLabel.${field}`;
  if (field && t.has(key)) return t(key);
  return field.replace(/_/g, " ");
}

/**
 * Provenance label for a conflict's `source`, reusing the SAME dictionary the
 * enrichment-history trail uses (`lib/enrichment-sources.ts`), never a second one.
 */
export function conflictSourceLabel(
  tProfile: Translator,
  source: string | null | undefined,
): string {
  if (!source) return "";
  const key = enrichmentSourceKey(source);
  return key ? tProfile(key) : source;
}

/**
 * Compose structured conflict facts into a localized heading + two
 * provenance-labeled value rows.
 *
 * `entity_label` is legitimately absent for a profile-level dispute
 * (`professional_summary` / `personal_info` have no entity) and for a stale id
 * whose entry was since edited away — both render the general heading rather
 * than a fabricated entry name.
 */
export function describeConflict(
  facts: ConflictFacts,
  t: Translator,
  tProfile: Translator,
): ConflictDescription {
  const field = conflictFieldLabel(t, facts);
  const heading = facts.entity_label
    ? t("conflictHeadingWithEntity", { entity: facts.entity_label, field })
    : t("conflictHeadingGeneral", { field });
  return {
    heading,
    existingRow: t("conflictValueRow", {
      label: t("conflictCurrentValueLabel"),
      value: facts.existing_value_display ?? "",
    }),
    incomingRow: t("conflictValueRow", {
      label: t("conflictNewValueLabel", {
        source: conflictSourceLabel(tProfile, facts.incoming_source),
      }),
      value: facts.incoming_value_display ?? "",
    }),
  };
}
