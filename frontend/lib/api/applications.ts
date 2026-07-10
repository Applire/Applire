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

export interface ApplicationPatchResult {
  id: string;
  user_status: string;
  applied_at: string | null;
  updated_at: string;
  submitted_cv_id?: string | null;
  submitted_cv_created_at?: string | null;
}

/**
 * PATCH /api/applications/{id} — set the user-managed pipeline status
 * (E039/US218). With `stampAppliedAt`, also records the submission moment;
 * only pass it on the FIRST transition to `applied` (applied_at is otherwise
 * user-owned and must not be silently overwritten). With `submittedCvId`,
 * additionally pins the sent CV version in the same PATCH (E039/US219 —
 * the post-download prompt knows exactly which version just went out).
 */
export async function patchApplicationStatus(
  applicationId: string,
  userStatus: string,
  opts: { stampAppliedAt?: boolean; submittedCvId?: string } = {},
): Promise<ApplicationPatchResult> {
  const body: Record<string, unknown> = { user_status: userStatus };
  if (opts.stampAppliedAt) {
    body.applied_at = new Date().toISOString();
  }
  if (opts.submittedCvId) {
    body.submitted_cv_id = opts.submittedCvId;
  }
  const res = await fetch(`${API_BASE}/api/applications/${applicationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`application patch ${res.status}`);
  return (await res.json()) as ApplicationPatchResult;
}

/**
 * PATCH /api/applications/{id} — pin (or with null: unpin) the submitted CV
 * version (E039/US219, journey Branch G). An explicit null is the backend's
 * clear semantics, so unpin must send `{"submitted_cv_id": null}`, not omit it.
 */
export async function patchSubmittedCv(
  applicationId: string,
  cvId: string | null,
): Promise<ApplicationPatchResult> {
  const res = await fetch(`${API_BASE}/api/applications/${applicationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ submitted_cv_id: cvId }),
  });
  if (!res.ok) throw new Error(`application patch ${res.status}`);
  return (await res.json()) as ApplicationPatchResult;
}

/** GET /api/applications/{id} — used by natural-moment prompts to check current state. */
export async function getApplication(
  applicationId: string,
): Promise<{
  id: string;
  user_status: string;
  applied_at: string | null;
  submitted_cv_id?: string | null;
  submitted_cv_created_at?: string | null;
}> {
  const res = await fetch(`${API_BASE}/api/applications/${applicationId}`);
  if (!res.ok) throw new Error(`application get ${res.status}`);
  return await res.json();
}
