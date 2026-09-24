// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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
 * ADR-090 — the review actions on a generated document (WORK-PACKAGES
 * Contract 2). One module for both kinds; `kind` is the URL segment.
 *
 * Every action answers with the refreshed report(s) and `review_state`, the
 * server having AWAITED the document re-audit: the surface re-renders from the
 * response, never from an optimistic guess.
 */

import type { ATSReport } from "@/lib/ats-report";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export type ReviewDocumentKind = "cv" | "cover-letter";

export type ReviewAction = "added" | "taken_out" | "edited";

/** ADR-090 cl. 6 — one decision on one finding of THIS generated document. */
export interface ReviewDecision {
  finding_key: string;
  label: string;
  action: ReviewAction;
  at: string;
  undo: { sections: Array<{ section_id: string; before: string }> } | null;
}

/** ADR-090 cl. 6 — the persisted per-document review state. `null` = none yet. */
export interface ReviewState {
  walked_at: string | null;
  decisions: ReviewDecision[];
}

/** The `TestimonyResult` the testimony service returns (door parity, ADR-058). */
export interface TestimonyOutcome {
  submission_id?: string;
  status:
    | "error"
    | "needs_confirmation"
    | "conflict"
    | "partial"
    | "applied"
    | "denial_recorded"
    | "no_change";
  changes?: unknown[];
  not_applied?: Array<{ span: string; kind: "figure" | "op"; reason: string }>;
  detail?: string | null;
}

export interface SectionChange {
  section_id: string;
  before: string;
  after: string;
}

/** The kind's ATS-report RESPONSE (as `GET …/ats-report` returns it). */
export interface ATSReportEnvelope {
  document_id?: string;
  status?: string;
  report: ATSReport;
  review_state?: ReviewState | null;
}

/** What every action returns: the refreshed reports and the state. */
export interface ReviewRefresh {
  /** Contract 2: the kind's ATS report RESPONSE — read it through `refreshedReport`. */
  report?: ATSReportEnvelope | ATSReport;
  /** `null` = no truthfulness report for this document; absent = unchanged. */
  truthfulness?: TruthfulnessReport | null;
  review_state: ReviewState | null;
}

export interface AddEvidenceResponse extends ReviewRefresh {
  testimony: TestimonyOutcome;
}

export interface TakeOutResponse extends ReviewRefresh {
  changes: SectionChange[];
  still_listed: boolean;
}

/** A failed request, carrying the HTTP status for `errors.generic`. */
export class ReviewActionError extends Error {
  constructor(public status: number) {
    super(`review action failed (${status})`);
  }
}

async function post<T>(kind: ReviewDocumentKind, id: string, action: string, body?: object): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/${kind}/${id}/review/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ReviewActionError(0);
  }
  if (!res.ok) throw new ReviewActionError(res.status);
  return (await res.json()) as T;
}

export function addEvidence(kind: ReviewDocumentKind, id: string, findingKey: string, text: string) {
  return post<AddEvidenceResponse>(kind, id, "add-evidence", { finding_key: findingKey, text });
}

export function takeOut(kind: ReviewDocumentKind, id: string, findingKey: string) {
  return post<TakeOutResponse>(kind, id, "take-out", { finding_key: findingKey });
}

export function undoDecision(kind: ReviewDocumentKind, id: string, findingKey: string) {
  return post<ReviewRefresh>(kind, id, "undo", { finding_key: findingKey });
}

/** After a section save that was opened from a finding (ADR-090 cl. 5). */
export function markEdited(kind: ReviewDocumentKind, id: string, findingKey: string) {
  return post<ReviewRefresh>(kind, id, "edited", { finding_key: findingKey });
}

export function markWalked(kind: ReviewDocumentKind, id: string) {
  return post<{ review_state: ReviewState | null }>(kind, id, "walked");
}

/**
 * The refreshed ATS report inside an action response. Contract 2 returns the
 * report RESPONSE (`{document_id, status, report, review_state}`); a bare report
 * is accepted too. `undefined` = the response carried no report.
 */
export function refreshedReport(refresh: ReviewRefresh): ATSReport | undefined {
  const r = refresh.report;
  if (r === undefined) return undefined;
  if (r && typeof r === "object" && "keywords" in r) return r as ATSReport;
  return (r as ATSReportEnvelope | null)?.report ?? null;
}

/** `review_state` of a response; `{}` (the backend's "none yet") reads as no decisions. */
export function refreshedState(refresh: ReviewRefresh): ReviewState | null {
  const s = refresh.review_state;
  if (!s) return null;
  return { walked_at: s.walked_at ?? null, decisions: s.decisions ?? [] };
}
