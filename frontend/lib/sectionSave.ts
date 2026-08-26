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
 * US290/US292 — PATCH /api/profile/{section} drivers, kept as pure async
 * functions (no React) so the 409/422/mismatch handling (H1.6, H0.4) is
 * unit-testable without mounting a dialog.
 *
 * Contract (backend, built in parallel on the leading branch):
 *  - List sections (`saveProfileSection`): PATCH with the COMPLETE section
 *    list as the body.
 *  - Object sections (`saveProfileObjectSection`, US292, #178): PATCH with a
 *    MERGE-PATCH body (RFC-7386 style) — only the keys the caller supplies.
 *    Supplied keys win, an explicit `null` CLEARS that field, and omitted
 *    keys survive untouched on the backend.
 *  - 200 -> the full profile response (same shape as GET /api/profile), both
 *    kinds.
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

/**
 * The backend stores `Certification.date_obtained` / `expiry_date` and
 * `Publication.published_date` as true dates and COMPLETES a partial
 * "YYYY-MM" (or "YYYY") to the first of the month/year on save
 * (`_coerce_partial_date`). The pickers send the partial shape, so the
 * value that comes back is longer than the one sent. That is the backend's
 * documented normalisation, not a lost write — the H0.4 detector must not
 * read it as one (integrator finding, real-browser pass 2026-08-26; the
 * schema-precision residual itself is #587).
 */
function completedPartialDate(sent: unknown, returned: unknown): boolean {
  if (typeof sent !== "string" || typeof returned !== "string") return false;
  if (/^\d{4}-\d{2}$/.test(sent)) return returned === `${sent}-01`;
  if (/^\d{4}$/.test(sent)) return returned === `${sent}-01-01`;
  return false;
}

/** Deep-equal by JSON, tolerating only the backend's partial-date completion. */
function entriesEquivalent(sent: Record<string, unknown>, returned: Record<string, unknown>): boolean {
  const keys = new Set([...Object.keys(sent), ...Object.keys(returned)]);
  for (const key of keys) {
    const a = sent[key];
    const b = returned[key];
    if (JSON.stringify(a) === JSON.stringify(b)) continue;
    if (completedPartialDate(a, b)) continue;
    return false;
  }
  return true;
}

function findById(list: unknown, id: string): Record<string, unknown> | undefined {
  if (!Array.isArray(list)) return undefined;
  return (list as Array<Record<string, unknown>>).find(
    (entry) => entry && typeof entry === "object" && entry.id === id,
  );
}

type RawPatchOutcome<TProfile extends ProfileSectionsResponseLike> =
  | { kind: "ok"; profile: TProfile }
  | { kind: "stale"; current: TProfile }
  | { kind: "invalid"; message: string }
  | { kind: "error" };

/**
 * Shared PATCH + status classification for both the list and object section
 * drivers below. Callers own the request body shape and the post-200
 * mismatch computation (the two kinds diff their bodies differently).
 */
async function patchSection<TProfile extends ProfileSectionsResponseLike>(
  apiBase: string,
  section: string,
  basisUpdatedAt: string,
  body: unknown,
  fetchImpl: typeof fetch | undefined,
): Promise<RawPatchOutcome<TProfile>> {
  const doFetch = fetchImpl ?? fetch;

  let res: Response;
  try {
    res = await doFetch(
      `${apiBase}/api/profile/${section}?basis_updated_at=${encodeURIComponent(basisUpdatedAt)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  } catch {
    return { kind: "error" };
  }

  if (res.status === 409) {
    let responseBody: unknown;
    try {
      responseBody = await res.json();
    } catch {
      return { kind: "error" };
    }
    const detail = (responseBody as { detail?: { error?: string; current?: unknown } } | undefined)?.detail;
    if (detail && detail.error === "stale_edit" && detail.current) {
      return { kind: "stale", current: detail.current as TProfile };
    }
    return { kind: "error" };
  }

  if (res.status === 422) {
    let responseBody: unknown;
    try {
      responseBody = await res.json();
    } catch {
      responseBody = undefined;
    }
    const detail = (responseBody as { detail?: unknown } | undefined)?.detail;
    const message = typeof detail === "string" ? detail : "";
    // A raw Pydantic validation dump ("1 validation error for
    // MasterProfileData\n...") is not user-facing text — it is multi-line,
    // English-only, and exposes internal schema names. Every caller's
    // `result.message || t("entryEditor.genericError")` fallback already
    // handles an empty message, so returning "" here routes every editor to
    // its translated generic error instead of rendering the dump verbatim
    // (adversarial finding 2026-08-26, F3).
    const isPydanticDump = message.includes("validation error for");
    return { kind: "invalid", message: isPydanticDump ? "" : message };
  }

  if (!res.ok) {
    return { kind: "error" };
  }

  let profile: TProfile;
  try {
    profile = (await res.json()) as TProfile;
  } catch {
    return { kind: "error" };
  }

  return { kind: "ok", profile };
}

function toSectionSaveResult<TProfile extends ProfileSectionsResponseLike>(
  outcome: RawPatchOutcome<TProfile>,
  computeMismatch: (profile: TProfile) => boolean,
): SectionSaveResult<TProfile> {
  switch (outcome.kind) {
    case "ok":
      return { status: "ok", profile: outcome.profile, mismatch: computeMismatch(outcome.profile) };
    case "stale":
      return { status: "stale", current: outcome.current };
    case "invalid":
      return { status: "invalid", message: outcome.message };
    case "error":
      return { status: "error" };
  }
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

  const outcome = await patchSection<TProfile>(apiBase, section, basisUpdatedAt, entries, fetchImpl);

  return toSectionSaveResult(outcome, (profile) => {
    if (!savedEntryId) return false;
    const sentEntry = findById(entries, savedEntryId);
    const returnedEntry = findById(profile.profile?.[section], savedEntryId);
    if (!returnedEntry) {
      // The entry we just saved isn't even present in the response — a
      // committer gate that dropped it is at least as bad as leaving it
      // byte-identical (H0.4's "unchanged vault" is the narrower case).
      return true;
    }
    if (sentEntry) {
      return !entriesEquivalent(sentEntry, returnedEntry);
    }
    return false;
  });
}

export interface SaveProfileObjectSectionParams {
  apiBase: string;
  section: string;
  /** Only the keys the caller actually changed — merge-patch body (#178). */
  patch: Record<string, unknown>;
  /** `updated_at` from the last GET this edit is based on. */
  basisUpdatedAt: string;
  /** Injectable for tests; defaults to the global `fetch`. */
  fetchImpl?: typeof fetch;
}

/**
 * PATCHes an OBJECT Master Profile section (`professional_summary`,
 * `personal_info`) as a merge-patch and classifies the result. Same
 * SectionSaveResult union as `saveProfileSection` — callers branch on
 * `status` identically regardless of which driver produced it.
 *
 * H0.4 for objects: for every key present in `patch`, compare
 * `JSON.stringify(sent)` against `JSON.stringify(profile.profile[section]?.[key])`,
 * treating `undefined` and `null` as equal — a cleared field legitimately
 * comes back `null`, not absent.
 */
export async function saveProfileObjectSection<TProfile extends ProfileSectionsResponseLike>(
  params: SaveProfileObjectSectionParams,
): Promise<SectionSaveResult<TProfile>> {
  const { apiBase, section, patch, basisUpdatedAt, fetchImpl } = params;

  const outcome = await patchSection<TProfile>(apiBase, section, basisUpdatedAt, patch, fetchImpl);

  return toSectionSaveResult(outcome, (profile) => {
    const returnedSection = (profile.profile?.[section] ?? {}) as Record<string, unknown>;
    return Object.keys(patch).some((key) => {
      const sent = patch[key] === undefined ? null : patch[key];
      const returned = returnedSection[key] === undefined ? null : returnedSection[key];
      return JSON.stringify(sent) !== JSON.stringify(returned);
    });
  });
}
