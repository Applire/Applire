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

// #702: `kept` — *So lassen* on a cross-document item (`critic:` key).
// ADR-099 (UAT-2 fix round, WP-C): `moved` / `as_goal` / `translated` — the
// class-A resolvers' edits (undoable); `no_change` — a "Stimmt das?" answer
// that changed nothing: an OPEN marker, never a closing decision.
export type ReviewAction =
  | "added"
  | "taken_out"
  | "edited"
  | "kept"
  | "supported"
  | "moved"
  | "as_goal"
  | "translated"
  | "no_change"
  | "shortened";

/** ADR-090 cl. 6 — one decision on one finding of THIS generated document. */
export interface ReviewDecision {
  finding_key: string;
  label: string;
  action: ReviewAction;
  at: string;
  undo: { sections: Array<{ section_id: string; before: string }> } | null;
  /** #797 (ruling D2-5): `supported` only — the profile lines that hold the wording. */
  receipt?: Array<{ path: string; text: string }>;
  /** U-13: `no_change` only — the reconciler found the answer's fact already in the profile. */
  found?: boolean;
}

/** ADR-090 cl. 6 — the persisted per-document review state. `null` = none yet. */
export interface ReviewState {
  walked_at: string | null;
  decisions: ReviewDecision[];
  /** ADR-099 cl. 5 — "Doch, stimmt – wo?" answers per removed claim (folded text → status). */
  removal_answers?: Record<string, { status: string; at?: string; backed?: boolean }>;
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
  /** R2-13 (build 4): `not_saved` = the reconcile failed after its retry; nothing was written. */
  error_code?: string | null;
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
  /** ADR-099 cl. 6 — the answer did not clear the finding: only "Entfernen" remains. */
  remove_only?: boolean;
  still_listed?: boolean;
}

/** ADR-099 cl. 6 — one answer of "Wohin gehört das?". */
export interface StationOption {
  station_id: string;
  experience_id?: string | null;
  title: string;
  organization: string;
}

export interface RemovalEvidenceResponse {
  testimony: TestimonyOutcome;
  review_state: ReviewState | null;
}

export interface SupportedResponse extends ReviewRefresh {
  /** The keyword check still lists the finding; the `supported` decision closes it anyway. */
  still_listed: boolean;
}

export interface TakeOutResponse extends ReviewRefresh {
  changes: SectionChange[];
  still_listed: boolean;
}

/** A failed request, carrying the HTTP status for `errors.generic`. */
export class ReviewActionError extends Error {
  /** ADR-099: the server's `detail.error` code (`resolver_unavailable`, `action_not_applicable`, `rewrite_failed`). */
  public code?: string;
  constructor(public status: number, code?: string) {
    super(`review action failed (${status})`);
    this.code = code;
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
  if (!res.ok) {
    let code: string | undefined;
    try {
      const body = (await res.json()) as { detail?: { error?: unknown } };
      if (body?.detail && typeof body.detail.error === "string") code = body.detail.error;
    } catch {
      /* no JSON body */
    }
    throw new ReviewActionError(res.status, code);
  }
  return (await res.json()) as T;
}

export function addEvidence(kind: ReviewDocumentKind, id: string, findingKey: string, text: string) {
  return post<AddEvidenceResponse>(kind, id, "add-evidence", { finding_key: findingKey, text });
}

/**
 * #797 (ruling D2-5 = A, ADR-090 cl. 6 amended) — *Stimmt* on a finding the
 * profile already holds: no testimony ask. Resolves `null` when the profile
 * does NOT hold every wording (409 `not_in_profile`) — the caller then asks.
 */
export async function confirmSupported(
  kind: ReviewDocumentKind,
  id: string,
  findingKey: string,
): Promise<SupportedResponse | null> {
  try {
    return await post<SupportedResponse>(kind, id, "supported", { finding_key: findingKey });
  } catch (e) {
    if (e instanceof ReviewActionError && e.status === 409) return null;
    throw e;
  }
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

/** #702 (RULING R-2): record (`keep`) or withdraw the `kept` decision on a cross-document item. */
export function keepCrossDocument(kind: ReviewDocumentKind, id: string, findingKey: string, keep = true) {
  return post<{ review_state: ReviewState | null }>(kind, id, "kept", { finding_key: findingKey, keep });
}

// ── ADR-099 resolvers (UAT-2 fix round, WP-C) ─────────────────────────────

/** "Wohin gehört das?" — the stations whose profile evidence holds the sentence. */
export async function listStations(kind: ReviewDocumentKind, id: string, findingKey: string) {
  const out = await post<{ stations: StationOption[] }>(kind, id, "stations", { finding_key: findingKey });
  return out.stations;
}

/** The answer = a station: Applire moves (CV) or re-attributes (letter) the sentence. */
export function moveToStation(kind: ReviewDocumentKind, id: string, findingKey: string, stationId: string) {
  return post<TakeOutResponse>(kind, id, "move", { finding_key: findingKey, station_id: stationId });
}

/** "Ist das erreicht?" → Nein: the sentence is rewritten as the goal. */
export function rewriteAsGoal(kind: ReviewDocumentKind, id: string, findingKey: string) {
  return post<TakeOutResponse>(kind, id, "as-goal", { finding_key: findingKey });
}

/** "Diese Stellen übersetzen" — only the named sentences; omitted = all of them. */
export function translateSpans(kind: ReviewDocumentKind, id: string, spans?: string[]) {
  return post<TakeOutResponse>(kind, id, "translate", spans ? { spans } : {});
}

/** "Jetzt prüfen" — re-run the full report on this version. */
export function recheckDocument(kind: ReviewDocumentKind, id: string) {
  return post<ReviewRefresh>(kind, id, "recheck");
}

/** "Doch, stimmt – wo?" on one removed claim. */
export function removalEvidence(kind: ReviewDocumentKind, id: string, removalText: string, text: string) {
  return post<RemovalEvidenceResponse>(kind, id, "removal-evidence", { removal_text: removalText, text });
}

/** The letter's page card (ADR-099 cl. 7, INTERFACE B-2): "Auf {n} Seite kürzen" — one corrector call, undoable. */
export function shortenLetter(id: string) {
  return post<TakeOutResponse>("cover-letter", id, "shorten");
}

/** The letter's page card: "{n} Seiten behalten" — the re-audit decides the row. */
export function keepLetterLength(id: string) {
  return post<ReviewRefresh>("cover-letter", id, "keep-length");
}

/** Decision key of the letter's page card. */
export const PAGE_LENGTH_KEY = "page:page-overflow";

/** Undo key of the translate decision (ADR-099 cl. 9). */
export const SEND_LANGUAGE_KEY = "send:document-language";

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
  return {
    walked_at: s.walked_at ?? null,
    decisions: s.decisions ?? [],
    ...(s.removal_answers ? { removal_answers: s.removal_answers } : {}),
  };
}
