// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * 783:195 (Hazelnut build 4) — the flow has moved past the interview and its
 * interview session is complete: the interview page shows that session's
 * summary again (read-only) instead of starting a new session and redirecting
 * to the CV. Before, the summary existed only on the completion screen, once.
 */
export interface ReopenFlowState {
  current_step: string;
  interview_summary?: { session_id: string; status?: string } | null;
}

export function reopenedSummarySessionId(fs: ReopenFlowState): string | null {
  const s = fs.interview_summary;
  if (!s || s.status !== "complete" || !s.session_id) return null;
  return fs.current_step === "cv_generation" || fs.current_step === "complete" ? s.session_id : null;
}
