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
 * Async CV import (E036 follow-up).
 *
 * A CV upload runs heavy segmented LLM work that, on a slow/output-capped model, used to
 * exceed the request/proxy timeout (504) and silently drop the CV. The backend now runs
 * that work in a background task: POST /api/profile/import-jobs returns a handle (202),
 * GET /api/profile/import-jobs/{id} is polled until ready/failed. This helper hides the
 * POST→poll so callers keep awaiting a single promise that resolves to the same
 * CVUploadResponse the old synchronous /upload returned.
 */

/** Subset of the backend CVUploadResponse that callers read; extra fields pass through. */
export interface CVUploadResult {
  profile_id: string | null;
  status: "DRAFT" | "COMPLETE" | "GATED";
  completeness_score: number;
  name_mismatch?: boolean;
  looks_like_cv?: boolean;
  undated_positions?: number;
  gate?: "none" | "not_a_cv" | "name_divergence";
  account_name?: string | null;
  cv_name?: string | null;
  staged_id?: string | null;
  [key: string]: unknown;
}

/** Carries the backend's stable error_code so callers can localize the message. */
export class CVImportError extends Error {
  constructor(public readonly errorCode: string | null) {
    super(errorCode ?? "import_failed");
    this.name = "CVImportError";
  }
}

interface UploadOptions {
  jobId?: string;
  apiBase?: string;
  /** Poll interval; defaults to 2500ms. */
  pollMs?: number;
  /** Hard ceiling so a never-updating job can't loop forever; defaults to 20 min. */
  maxWaitMs?: number;
  signal?: AbortSignal;
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const id = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(id);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

/**
 * Upload a single CV and resolve once the background import finishes.
 *
 * Resolves with the CVUploadResponse on success (including a GATED merge — callers
 * branch on `status === "GATED"`). Rejects with a {@link CVImportError} carrying the
 * backend error_code on a failed import (llm_truncated, llm_timeout, invalid_document…).
 */
export async function uploadCvAsync(file: File, opts: UploadOptions = {}): Promise<CVUploadResult> {
  const base = opts.apiBase ?? "";
  const pollMs = opts.pollMs ?? 2500;
  const maxWaitMs = opts.maxWaitMs ?? 20 * 60 * 1000;

  const form = new FormData();
  form.append("file", file);
  const query = opts.jobId ? `?job_id=${encodeURIComponent(opts.jobId)}` : "";

  const startRes = await fetch(`${base}/api/profile/import-jobs${query}`, {
    method: "POST",
    body: form,
    signal: opts.signal,
  });
  if (!startRes.ok) throw new CVImportError("import_failed");
  const { import_id: importId } = (await startRes.json()) as { import_id: string };

  const deadline = Date.now() + maxWaitMs;
  for (;;) {
    // Poll immediately (no leading delay) so an already-finished job resolves at once;
    // delay only between subsequent polls.
    const res = await fetch(`${base}/api/profile/import-jobs/${importId}`, { signal: opts.signal });
    if (res.ok) {
      const data = (await res.json()) as {
        status: string;
        error_code: string | null;
        result: CVUploadResult | null;
      };
      if (data.status === "ready" && data.result) return data.result;
      if (data.status === "failed") throw new CVImportError(data.error_code);
      // pending | processing → keep polling
    }
    if (Date.now() > deadline) throw new CVImportError("import_timeout");
    await delay(pollMs, opts.signal);
  }
}
