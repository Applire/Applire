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

import { getApiErrorMessage } from "./errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export interface GapItem {
  id: string;
  label: string;
  status: "pending" | "active" | "done" | "na" | "skipped";
}

export interface EnrichSession {
  session_id: string;
  first_question: string;
  gaps: GapItem[];
  estimated_questions: number;
}

// US166 — the completeness panel surfaces section-level gaps, but the Mode C
// engine keys off finer work-entry gaps; when none remain the backend 404s.
// We model that as a benign "nothing to enrich" sentinel rather than an error,
// so launching enrichment from the health hub can land on a friendly state.
export interface EnrichNoGaps {
  noGaps: true;
}

export function isEnrichNoGaps(
  result: EnrichSession | EnrichNoGaps,
): result is EnrichNoGaps {
  return "noGaps" in result;
}

export interface EnrichRespondResult {
  next_question: string | null;
  gaps: GapItem[];
  done: boolean;
  profile_updated: boolean;
}

export interface EnrichActionResult {
  next_question: string | null;
  gaps: GapItem[];
  done: boolean;
}

export async function startEnrichSession(
  scope?: string,
): Promise<EnrichSession | EnrichNoGaps> {
  const res = await fetch(`${API_BASE}/api/profile/enrich/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: scope ?? null }),
  });
  // 404 = no completeness gaps to enrich (or no profile): a benign state, not
  // an error to surface in red. Every other non-2xx is a genuine failure.
  if (res.status === 404) {
    return { noGaps: true };
  }
  if (!res.ok) {
    const errorMsg = await getApiErrorMessage(res);
    throw new Error(errorMsg);
  }
  return res.json();
}

export async function respondToEnrich(
  sessionId: string,
  answer: string
): Promise<EnrichRespondResult> {
  const res = await fetch(`${API_BASE}/api/profile/enrich/${sessionId}/respond`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  if (!res.ok) {
    const errorMsg = await getApiErrorMessage(res);
    throw new Error(errorMsg);
  }
  return res.json();
}

export async function skipGap(sessionId: string): Promise<EnrichActionResult> {
  const res = await fetch(`${API_BASE}/api/profile/enrich/${sessionId}/skip`, {
    method: "POST",
  });
  if (!res.ok) {
    const errorMsg = await getApiErrorMessage(res);
    throw new Error(errorMsg);
  }
  return res.json();
}

export async function markGapNA(sessionId: string): Promise<EnrichActionResult> {
  const res = await fetch(`${API_BASE}/api/profile/enrich/${sessionId}/na`, {
    method: "POST",
  });
  if (!res.ok) {
    const errorMsg = await getApiErrorMessage(res);
    throw new Error(errorMsg);
  }
  return res.json();
}
