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

interface StartOptions {
  jobId?: string;
  apiBase?: string;
  signal?: AbortSignal;
}

interface PollOptions {
  apiBase?: string;
  /** Poll interval; defaults to 2500ms. */
  pollMs?: number;
  /** Hard ceiling so a never-updating job can't loop forever; defaults to 20 min. */
  maxWaitMs?: number;
  signal?: AbortSignal;
}

type UploadOptions = StartOptions & PollOptions;

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
 * Start an async CV import: POST the file and resolve with the import job's id.
 *
 * Split from the poll (PQ F1) so a multi-file flow can queue EVERY file on the server
 * before any long wait begins — once all POSTs are through, a refresh can no longer
 * lose queued files; the backend finishes the jobs without the client. Rejects with a
 * {@link CVImportError} if the job could not be created.
 */
export async function startCvImport(file: File, opts: StartOptions = {}): Promise<string> {
  const base = opts.apiBase ?? "";
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
  return importId;
}

/**
 * Poll an already-started import job until it finishes.
 *
 * Resolves with the CVUploadResponse on success (including a GATED merge — callers
 * branch on `status === "GATED"`). Rejects with a {@link CVImportError} carrying the
 * backend error_code on a failed import (llm_truncated, llm_timeout, invalid_document…).
 */
export async function pollCvImport(importId: string, opts: PollOptions = {}): Promise<CVUploadResult> {
  const base = opts.apiBase ?? "";
  const pollMs = opts.pollMs ?? 2500;
  const maxWaitMs = opts.maxWaitMs ?? 20 * 60 * 1000;

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

/**
 * Upload a single CV and resolve once the background import finishes
 * (start + poll in one await — the single-file convenience used by retries
 * and other call sites).
 */
export async function uploadCvAsync(file: File, opts: UploadOptions = {}): Promise<CVUploadResult> {
  const importId = await startCvImport(file, opts);
  return pollCvImport(importId, opts);
}
