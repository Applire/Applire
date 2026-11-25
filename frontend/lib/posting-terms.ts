// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Sorbet WP-Q10 (ruling Q7-2, Q10-1…Q10-3) — the posting-terms tick list.
 *
 * The backend decides which terms the card asks about (`GET
 * /api/job/{jobId}/posting-terms`); the card's answer is the ticked terms
 * (`POST`), every other term shown is declined for this application. An empty
 * answer is valid. Every web entry to CV generation reads `openTerms` first.
 */

export interface PostingTerm {
  term: string;
  /** The person's own words: held forms, then the verified backing quote. */
  their_words: string[];
}

export interface PostingTermsState {
  terms: PostingTerm[];
  declined: string[];
  left_open: string[];
  /** The terms the last answer added to the profile — the receipt. */
  confirmed: string[];
  answered_at: string | null;
  language: string | null;
}

import { refuseApplicationBeforeTicks } from "@/components/plan/events";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

/** The card's list; `null` when it cannot be read — the caller then proceeds as
 * today (the generation seam never prints an unticked term either way). */
export async function fetchPostingTerms(jobId: string): Promise<PostingTermsState | null> {
  try {
    const res = await fetch(`${API_BASE}/api/job/${jobId}/posting-terms`);
    if (!res.ok) return null;
    return (await res.json()) as PostingTermsState;
  } catch {
    return null;
  }
}

/** The person's answer. Throws when it was not stored, so the CV button does not
 * move on as if the ticks had been applied. */
export async function answerPostingTerms(jobId: string, confirmed: string[]): Promise<PostingTermsState> {
  const res = await fetch(`${API_BASE}/api/job/${jobId}/posting-terms`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed }),
  });
  if (!res.ok) throw new Error(`posting-terms answer failed: ${res.status}`);
  return (await res.json()) as PostingTermsState;
}

/** Are there terms to put to the person before a CV is generated? */
export function hasOpenTerms(state: PostingTermsState | null | undefined): state is PostingTermsState {
  return !!state && Array.isArray(state.terms) && state.terms.length > 0;
}

/** The receipt's term list in the UI's list style ("A, B, C"). */
export function receiptTerms(confirmed: string[]): string {
  return confirmed.join(", ");
}

/** Where the profile reads "where this skill came from" — one line per
 * confirmation; the wording differs when the confirmed name is a second name. */
export function confirmationKey(
  skillName: string,
  confirmation: { name: string; via: "person" | "agent" },
): "byYou" | "byAgent" | "aliasByYou" | "aliasByAgent" {
  const same = confirmation.name.trim().toLocaleLowerCase() === skillName.trim().toLocaleLowerCase();
  if (confirmation.via === "agent") return same ? "byAgent" : "aliasByAgent";
  return same ? "byYou" : "aliasByYou";
}

/**
 * DH item 5 (Sorbet invite-gate delivery run, 2026-11-25) — the order before a CV is
 * started from the web: FIRST the allowance (a refused application opens its dialog
 * and the generation stops: `"refused"`), THEN the posting-terms list (`state`, or
 * `null` when there is nothing to ask). At 0 remaining the person was asked the whole
 * tick list and refused only after answering it.
 */
export async function termsBeforeGeneration(jobId: string): Promise<"refused" | PostingTermsState | null> {
  if (await refuseApplicationBeforeTicks(jobId)) return "refused";
  const terms = await fetchPostingTerms(jobId);
  return hasOpenTerms(terms) ? terms : null;
}
