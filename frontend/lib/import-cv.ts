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

/**
 * One incoming CV-import entry the merge's own ops did not carry (#615,
 * ADR-063 amended 2026-08-28). FACTS only — no item here is proof of loss
 * (see the backend's `import_witness.compute_import_not_applied` docstring
 * for the carried-predicate and its named false-positive shapes).
 */
export interface ImportNotAppliedItem {
  section: string | null;
  label: string;
  reason: "no_op_carried_entry" | "op_rejected";
}

/**
 * One entry the import recognised as already in the profile under another name
 * (#707 receipt; #717 shows it on the import summary). `basis`: who
 * recognised it — the model, a recorded alternate name, or the closed DE/EN
 * language-name table (a `name_table` pair is a fact and has no undo).
 */
export interface MatchReceiptItem {
  section: string;
  entity_id: string;
  incoming: string;
  existing: string;
  basis?: "model" | "alias" | "name_table";
  aliases_added?: Record<string, string>;
  incoming_entry?: Record<string, unknown> | null;
  undone_at?: string | null;
}

/** D-B3V6 (ruling V-3 = A) — a second-source bullet the import's restatement
 * check held back: `text` restates `restates`, a bullet the entry already holds. */
export interface RestatedReceiptItem {
  entity_id: string;
  entity_label?: string | null;
  section: string;
  field: "responsibilities" | "achievements";
  text: string;
  restates: string;
}

/** Subset of the backend CVUploadResponse that callers read; extra fields pass through. */
export interface CVUploadResult {
  profile_id: string | null;
  status: "DRAFT" | "COMPLETE" | "GATED";
  completeness_score: number;
  name_mismatch?: boolean;
  looks_like_cv?: boolean;
  undated_positions?: number;
  gate?: "none" | "not_a_cv" | "name_divergence" | "review_not_run";
  account_name?: string | null;
  cv_name?: string | null;
  staged_id?: string | null;
  /** #615 — "the merge ran AND nothing is known to be missing" vs "the merge
   * ran but the carried-predicate names entries it did not carry". */
  merge_status?: "applied" | "partial";
  not_applied?: ImportNotAppliedItem[];
  /** #717 — the merge's recognised pairs (same receipt the history shows). */
  matched?: MatchReceiptItem[];
  /** D-B3V6 — bullets held back as restatements of a held bullet. */
  restated?: RestatedReceiptItem[];
  /** R3-2 = B — the source was longer than one import reads: only the first
   * `read_chars` of `total_chars` characters were extracted. `null` when not. */
  source_truncated?: SourceTruncation | null;
  [key: string]: unknown;
}

export interface SourceTruncation {
  read_chars: number;
  total_chars: number;
}

/**
 * R3-2 = B: the share of an over-long source the import read, as a whole
 * percent kept inside 1..99 (a cut file never reads as "0 %" or "100 %").
 * `null` when nothing was cut or the fact is malformed.
 */
export function sourceTruncatedPercent(t: SourceTruncation | null | undefined): number | null {
  if (!t || !(t.total_chars > 0) || !(t.read_chars >= 0)) return null;
  return Math.min(99, Math.max(1, Math.round((t.read_chars / t.total_chars) * 100)));
}

/** Carries the backend's stable error_code so callers can localize the message. */
export class CVImportError extends Error {
  constructor(public readonly errorCode: string | null) {
    super(errorCode ?? "import_failed");
    this.name = "CVImportError";
  }
}

/**
 * The `processing` catalog key that names an import failure to the user — never the
 * raw backend text. `ocr_unavailable`: an image/scan on an instance without a runnable
 * OCR backend (#789 L12). `file_too_large`: over the 10 MB cap (#789 L13).
 */
export function importErrorMessageKey(
  code: string | null,
): "uploadTooLarge" | "uploadNoOcr" | "uploadTryAgain" | "uploadNotSaved" | "uploadFailed" {
  if (code === "file_too_large") return "uploadTooLarge";
  // R2-13 — the merge call failed after its retry; nothing was changed.
  if (code === "not_saved") return "uploadNotSaved";
  if (code === "ocr_unavailable") return "uploadNoOcr";
  if (code === "llm_truncated" || code === "llm_timeout") return "uploadTryAgain";
  return "uploadFailed";
}

interface StartOptions {
  /**
   * Tells the backend this file is a LinkedIn profile export (#789 L31). A ZIP is a
   * LinkedIn export by construction; a PDF is ambiguous (CV or LinkedIn print), so it
   * only takes the LinkedIn adapter when this is set. Omitted = a CV.
   */
  source?: "linkedin";
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
  const params = new URLSearchParams();
  if (opts.jobId) params.set("job_id", opts.jobId);
  if (opts.source) params.set("source", opts.source);
  const query = params.size > 0 ? `?${params.toString()}` : "";

  const startRes = await fetch(`${base}/api/profile/import-jobs${query}`, {
    method: "POST",
    body: form,
    signal: opts.signal,
  });
  // 413: the REST size cap (backend `file_too_large`) or the reverse proxy's own body
  // limit — either way the file is too big, and the message should say so (#789 L13).
  if (startRes.status === 413) throw new CVImportError("file_too_large");
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
