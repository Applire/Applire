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
 * Async gap analysis (E037 N2).
 *
 * The first gap analysis of a fresh job runs heavy real-LLM work that used to block the
 * gaps screen ~2 min and 504 fragilely (a mid-call timeout wedged it until a manual
 * reload). The backend now runs that work in a background task: POST
 * /api/job/{jobId}/gap-jobs returns a handle (202), GET
 * /api/job/{jobId}/gap-jobs/{gapJobId} is polled until ready/failed. This helper hides
 * the POST→poll so callers await a single promise resolving to the GapAnalysisResponse.
 */

/** Subset of the backend GapAnalysisResponse callers read; extra fields pass through. */
export interface GapAnalysisResult {
  id: string;
  match_score: number | null;
  [key: string]: unknown;
}

/** Carries the backend's stable error_code so callers can localize the message. */
export class GapAnalysisError extends Error {
  constructor(public readonly errorCode: string | null) {
    super(errorCode ?? "gap_failed");
    this.name = "GapAnalysisError";
  }
}

interface GapAnalysisOptions {
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
 * Kick off gap analysis for a job and resolve once the background analysis finishes.
 *
 * Resolves with the GapAnalysisResponse on success. Rejects with a {@link GapAnalysisError}
 * carrying the backend error_code on a failed analysis (llm_timeout, rate_limited, …) or
 * "gap_timeout" past maxWaitMs.
 */
export async function analyzeGapsAsync(
  jobId: string,
  opts: GapAnalysisOptions = {},
): Promise<GapAnalysisResult> {
  const base = opts.apiBase ?? "";
  const pollMs = opts.pollMs ?? 2500;
  const maxWaitMs = opts.maxWaitMs ?? 20 * 60 * 1000;

  const startRes = await fetch(`${base}/api/job/${jobId}/gap-jobs`, {
    method: "POST",
    signal: opts.signal,
  });
  if (!startRes.ok) throw new GapAnalysisError("gap_failed");
  const { gap_job_id: gapJobId } = (await startRes.json()) as { gap_job_id: string };

  const deadline = Date.now() + maxWaitMs;
  for (;;) {
    // Poll immediately (no leading delay) so an already-finished/idempotent job resolves
    // at once; delay only between subsequent polls.
    const res = await fetch(`${base}/api/job/${jobId}/gap-jobs/${gapJobId}`, {
      signal: opts.signal,
    });
    if (res.ok) {
      const data = (await res.json()) as {
        status: string;
        error_code: string | null;
        result: GapAnalysisResult | null;
      };
      if (data.status === "ready" && data.result) return data.result;
      if (data.status === "failed") throw new GapAnalysisError(data.error_code);
      // pending | processing → keep polling
    }
    if (Date.now() > deadline) throw new GapAnalysisError("gap_timeout");
    await delay(pollMs, opts.signal);
  }
}
