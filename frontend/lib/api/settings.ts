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
