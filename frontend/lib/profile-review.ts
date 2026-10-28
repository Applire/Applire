// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// Hazelnut build 5 (UAT-1, U-2, U-4/U-5; ADR-099 cl. 11) — the profile-review
// client with STRUCTURED conflict outcomes. The conflict on screen arrives as
// `current_conflict` (its own facts + the outcome set the backend allows for its
// shape); an answer chip sends `{outcome}` — never its label through the
// free-text matcher (the build-4 "Beide behalten" defect: the KEEP_BOTH_INTENT
// sentence was read as keep-existing). A typed answer is still `{message}` and
// the response says how it was read (`interpreted`).

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export type ConflictOutcome = "keep_current" | "take_new" | "keep_both" | "merge";
/** The wire value a confirmed merge is committed with. */
export type CommitOutcome = Exclude<ConflictOutcome, "merge"> | "merge_confirm";

export interface CurrentConflict {
  conflict_id: string;
  shape: "list" | "scalar";
  outcomes: ConflictOutcome[];
  entity_label?: string | null;
  section?: string | null;
  field?: string | null;
  existing_value_display?: string | null;
  incoming_value_display?: string | null;
  incoming_source?: string | null;
}

export interface Interpreted {
  outcome?: CommitOutcome | ConflictOutcome | null;
  corrected_value?: string | null;
  follow_up?: string | null;
  /** A merged text that failed the server's fact check went in as testimony. */
  testimony_applied?: boolean | null;
}

export interface ReviewTurn {
  question?: string | null;
  choices: string[] | null;
  current_conflict?: CurrentConflict | null;
  follow_up_used?: boolean;
  review_section?: string | null;
  current_gap_id?: string | null;
}

export interface ReviewStart extends ReviewTurn {
  session_id: string;
  first_question: string | null;
  gaps_total: number;
  gaps_remaining: number;
}

export interface ReviewReply extends ReviewTurn {
  complete: boolean;
  gaps_remaining: number | null;
  interpreted?: Interpreted | null;
}

export interface MergeProposal {
  proposal: string | null;
  verifier: "passed" | "failed";
  sources?: string[];
}

/** A request the backend refused with a machine code (`outcome_required`, …). */
export class ReviewApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string | null,
    message: string,
  ) {
    super(message);
  }
}

async function errorOf(res: Response): Promise<ReviewApiError> {
  let code: string | null = null;
  let message = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") {
      code = detail;
      message = detail;
    } else if (detail && typeof detail === "object") {
      code = typeof detail.code === "string" ? detail.code : null;
      message = typeof detail.message === "string" ? detail.message : (code ?? message);
    }
  } catch {
    /* non-JSON body: keep the status line */
  }
  return new ReviewApiError(res.status, code, message);
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await errorOf(res);
  return res.json() as Promise<T>;
}

/** U-2 — start on the issue the user clicked (`HealthIssue.id`); an unknown or
 *  already-closed id falls back to the generic walk on the server. */
export function startReview(issueId?: string | null): Promise<ReviewStart> {
  return post<ReviewStart>("/api/session/profile-review", issueId ? { issue_id: issueId } : {});
}

export function answerWithOutcome(
  sessionId: string,
  outcome: CommitOutcome,
  mergedText?: string,
): Promise<ReviewReply> {
  return post<ReviewReply>(
    `/api/session/${sessionId}/message`,
    mergedText !== undefined ? { outcome, merged_text: mergedText } : { outcome },
  );
}

export function answerWithText(sessionId: string, message: string): Promise<ReviewReply> {
  return post<ReviewReply>(`/api/session/${sessionId}/message`, { message });
}

export function requestMergeProposal(sessionId: string, conflictId: string): Promise<MergeProposal> {
  return post<MergeProposal>(`/api/session/${sessionId}/merge-proposal`, { conflict_id: conflictId });
}

/**
 * The outcomes to render for a conflict. The backend's set is authoritative
 * (list → four, role-entry scalar → three, other scalar → two); `merge` drops
 * out once a proposal failed the fact check (ADR-099 cl. 11: fail closed →
 * only the other three).
 */
export function visibleOutcomes(
  conflict: Pick<CurrentConflict, "outcomes">,
  mergeUnavailable: boolean,
): ConflictOutcome[] {
  return conflict.outcomes.filter((o) => !(o === "merge" && mergeUnavailable));
}

/** The outcome a typed answer was read as, normalised to the chip it matches. */
export function interpretedOutcome(i: Interpreted | null | undefined): ConflictOutcome | null {
  const o = i?.outcome ?? null;
  if (o === "merge_confirm") return "merge";
  return o;
}
