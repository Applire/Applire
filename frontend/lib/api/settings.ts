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

import type { ReviewModePreference } from "@/lib/review-walked";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export interface AppSettings {
  default_color_profile_id: string | null;
  default_accent_hex: string | null;
  ui_language: "de" | "en";
  hide_predownload_notice: boolean;
  // E042/US239 (ADR-051 §1): the user's default CV page target. null = region standard.
  target_cv_pages: number | null;
  // E058/US301 (ADR-081 cl. 5): how the document review surface presents its
  // findings. `auto` — the default — makes the mode a property of the DOCUMENT
  // rather than of the person. Optional here for back-compat with a backend
  // that predates the column; the reader falls back to `auto`.
  review_mode?: ReviewModePreference;
  // #679 — first-use explainers the user has dismissed for good, by id
  // (`fact_pins_intro` is the first). Optional here for back-compat with a
  // backend that predates the column; a reader that gets nothing must treat
  // it as "nothing dismissed" and SHOW the explainer (fail-open, D-3).
  dismissed_explainers?: string[];
}

/** GET /api/settings — the current user's preferences. */
export async function getSettings(): Promise<AppSettings> {
  const res = await fetch(`${API_BASE}/api/settings`);
  if (!res.ok) throw new Error(`settings ${res.status}`);
  return (await res.json()) as AppSettings;
}

/**
 * Persist the shared "don't show the pre-download notice again" preference
 * (ADR-040 amendment). Best-effort — a failure must not block the download.
 */
export async function setHidePredownloadNotice(hide: boolean): Promise<void> {
  await fetch(`${API_BASE}/api/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hide_predownload_notice: hide }),
  });
}

/**
 * Persist the user's default CV page target (E042/US239, ADR-051 §1).
 * `null` clears the override, falling back to the region standard.
 */
export async function setTargetCvPages(pages: number | null): Promise<void> {
  await fetch(`${API_BASE}/api/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_cv_pages: pages }),
  });
}

/**
 * Persist the user's review-mode preference (E058/US301, ADR-081 cl. 5).
 * Deliberately NOT on the agent door (cl. 8, an explicit SF-DOOR.4 carve-out):
 * an agent consumes the structured report, it has no reading preference.
 */
export async function setReviewMode(mode: ReviewModePreference): Promise<void> {
  await fetch(`${API_BASE}/api/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_mode: mode }),
  });
}

/**
 * Dismiss one first-use explainer for good (#679). Additive and idempotent on
 * the backend, which validates the id against its own allowlist (D-7) — the
 * response carries the updated list, which this caller does not need.
 *
 * Best-effort by design (D-3): the user has already seen the explainer and
 * clicked past it, so a failed write must never surface as an error. One
 * silent retry covers the transient case; a second failure just means the
 * explainer appears once more.
 */
export async function dismissExplainer(explainerId: string): Promise<void> {
  const send = () =>
    fetch(`${API_BASE}/api/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dismiss_explainer: explainerId }),
    });
  try {
    const res = await send();
    if (res.ok) return;
  } catch {
    // fall through to the single retry
  }
  try {
    await send();
  } catch {
    // swallowed: never a user-visible error (D-3)
  }
}
