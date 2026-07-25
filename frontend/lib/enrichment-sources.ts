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

/**
 * US256 (E045) — i18n label keys for `EnrichmentRecord.source`.
 *
 * Mirrors the backend Literal in `backend/applire/schemas/profile.py`
 * (EnrichmentRecord.source) plus the E045 `agent_interview` value written by
 * the `submit_claims` MCP tool. Keys are relative to the `profile` next-intl
 * namespace (`t("sources.<value>")`); labels live in messages/de.json and
 * messages/en.json under `profile.sources`.
 *
 * When the backend Literal grows, add the value here AND in both catalogs —
 * `lib/__tests__/enrichment-sources.test.ts` enforces the three-way sync.
 */
export const ENRICHMENT_SOURCE_KEYS: Record<string, string> = {
  cv_upload: "sources.cv_upload",
  cv_paste: "sources.cv_paste",
  linkedin_import: "sources.linkedin_import",
  xing_import: "sources.xing_import",
  interview: "sources.interview",
  manual_edit: "sources.manual_edit",
  manual_role_add: "sources.manual_role_add",
  agent_interview: "sources.agent_interview",
  testimony: "sources.testimony",
};

/**
 * Namespaced i18n key for a source value, or null for an unknown (future)
 * value — the caller then falls back to rendering the raw string rather than
 * crashing on a missing translation key.
 */
export function enrichmentSourceKey(source: string): string | null {
  return ENRICHMENT_SOURCE_KEYS[source] ?? null;
}
