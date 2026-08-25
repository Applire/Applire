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
 * US290 — PATCH /api/profile/{section} driver, kept as a pure async function
 * (no React) so the 409/422/mismatch handling (H1.6, H0.4) is unit-testable
 * without mounting a dialog.
 *
 * Contract (backend, built in parallel on the leading branch):
 *  - PATCH /api/profile/{section}?basis_updated_at=<url-encoded ISO> with the
 *    COMPLETE section list as the body.
 *  - 200 -> the full profile response (same shape as GET /api/profile).
 *  - 409 -> {"detail": {"error": "stale_edit", "current": <full profile>}}
 *    (FastAPI wraps HTTPException.detail — the error code and the reload
 *    payload both live UNDER `detail`, not `detail` itself).
 *  - 422 -> {"detail": "<string>"}.
 */

export interface ProfileSectionsResponseLike {
  updated_at: string;
  profile: Record<string, unknown>;
  [key: string]: unknown;
}

export type SectionSaveResult<TProfile extends ProfileSectionsResponseLike> =
  | { status: "ok"; profile: TProfile; mismatch: boolean }
  | { status: "stale"; current: TProfile }
  | { status: "invalid"; message: string }
  | { status: "error" };

export interface SaveProfileSectionParams {
  apiBase: string;
  section: string;
  /** The COMPLETE next section list (existing entries + the one edited/added/removed). */
  entries: unknown[];
  /** `updated_at` from the last GET this edit is based on. */
  basisUpdatedAt: string;
  /**
   * The `id` of the entry the user actually edited/added, used for the
   * post-save H0.4 mismatch detector. Omit for a brand-new entry (it has no
   * id yet) or a pure removal (nothing to compare).
   */
  savedEntryId?: string | null;
  /** Injectable for tests; defaults to the global `fetch`. */
  fetchImpl?: typeof fetch;
}

function findById(list: unknown, id: string): Record<string, unknown> | undefined {
  if (!Array.isArray(list)) return undefined;
  return (list as Array<Record<string, unknown>>).find(
    (entry) => entry && typeof entry === "object" && entry.id === id,
  );
}

/**
 * PATCHes a whole Master Profile section and classifies the result.
 *
 * H0.4: on a 200, the entry the user saved (matched by `id`) is compared
 * against the same entry in the response. A committer gate that returns 200
 * without actually persisting the change surfaces here as `mismatch: true`
 * rather than a silent false "saved".
 */
export async function saveProfileSection<TProfile extends ProfileSectionsResponseLike>(
  params: SaveProfileSectionParams,
): Promise<SectionSaveResult<TProfile>> {
  const { apiBase, section, entries, basisUpdatedAt, savedEntryId, fetchImpl } = params;
  const doFetch = fetchImpl ?? fetch;

  let res: Response;
  try {
    res = await doFetch(
      `${apiBase}/api/profile/${section}?basis_updated_at=${encodeURIComponent(basisUpdatedAt)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entries),
      },
    );
  } catch {
    return { status: "error" };
  }

  if (res.status === 409) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      return { status: "error" };
    }
    const detail = (body as { detail?: { error?: string; current?: unknown } } | undefined)?.detail;
    if (detail && detail.error === "stale_edit" && detail.current) {
      return { status: "stale", current: detail.current as TProfile };
    }
    return { status: "error" };
  }

  if (res.status === 422) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = undefined;
    }
    const detail = (body as { detail?: unknown } | undefined)?.detail;
    return { status: "invalid", message: typeof detail === "string" ? detail : "" };
  }

  if (!res.ok) {
    return { status: "error" };
  }

  let profile: TProfile;
  try {
    profile = (await res.json()) as TProfile;
  } catch {
    return { status: "error" };
  }

  let mismatch = false;
  if (savedEntryId) {
    const sentEntry = findById(entries, savedEntryId);
    const returnedEntry = findById(profile.profile?.[section], savedEntryId);
    if (!returnedEntry) {
      // The entry we just saved isn't even present in the response — a
      // committer gate that dropped it is at least as bad as leaving it
      // byte-identical (H0.4's "unchanged vault" is the narrower case).
      mismatch = true;
    } else if (sentEntry) {
      mismatch = JSON.stringify(sentEntry) !== JSON.stringify(returnedEntry);
    }
  }

  return { status: "ok", profile, mismatch };
}
