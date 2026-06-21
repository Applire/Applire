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

// US165 (E033) — client for the standalone profile-review interview (no JD).
// Drives the real session engine: launch via /api/session/profile-review, then
// answer each open conflict via /api/session/{id}/message.

import { getApiErrorMessage } from "./errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export interface ProfileReviewSession {
  session_id: string;
  first_question: string;
  gaps_total: number;
  gaps_remaining: number;
  choices: string[] | null;
}

export interface ProfileReviewMessageResult {
  complete: boolean;
  question: string | null;
  choices: string[] | null;
  gaps_remaining: number | null;
}

export async function startProfileReview(): Promise<ProfileReviewSession> {
  const res = await fetch(`${API_BASE}/api/session/profile-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    throw new Error(await getApiErrorMessage(res));
  }
  return res.json();
}

export async function sendProfileReviewMessage(
  sessionId: string,
  message: string,
): Promise<ProfileReviewMessageResult> {
  const res = await fetch(`${API_BASE}/api/session/${sessionId}/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) {
    throw new Error(await getApiErrorMessage(res));
  }
  return res.json();
}
