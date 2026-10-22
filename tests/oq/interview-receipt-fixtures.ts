// Copyright (C) 2026 Tobias Rosenbaum
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
 * #763 — page.route stubs for the interview page's no-write receipt (desktop
 * and mobile OQ). Synthetic data only; no backend is reached.
 */
import type { Page } from "@playwright/test";

export const FLOW_ID = "f7630000-0000-0000-0000-000000000001";
const JOB_ID = "f7630000-0000-0000-0000-0000000000a1";
const SESSION_ID = "f7630000-0000-0000-0000-0000000000b1";

const cluster = (id: string, label: string, skills: string[]) => ({
  id,
  label,
  category: "C",
  gaps: skills,
  jd_skills: skills,
  jd_context: "",
  outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
  coverage: "open",
  budget_remaining: 2,
  member_statuses: skills.map((member) => ({ member, status: "gap" })),
});

export const ANSWER =
  "At Nordwerk Logistik I moved our nightly batch jobs to Airflow in 2023 and cut the run time from six hours to ninety minutes.";

/**
 * Stub the interview's endpoints. `turn` is the POST /message response body;
 * pass `no_write_receipt` to exercise the receipt.
 */
export async function stubInterview(page: Page, turn: Record<string, unknown>): Promise<void> {
  await page.route(`**/api/flow/${FLOW_ID}/state`, (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: JOB_ID,
        current_step: "interview",
        job_summary: { role_title: "Senior Data Engineer" },
        interview_summary: null,
      }),
    }),
  );
  await page.route(`**/api/job/${JOB_ID}/gaps`, (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "ga-1",
        match_score: 0.62,
        gap_clusters: [
          cluster("c-orch", "Workflow orchestration", ["Airflow", "Dagster"]),
          cluster("c-stream", "Stream processing", ["Kafka", "Flink"]),
          cluster("c-cloud", "Cloud data platforms", ["BigQuery"]),
        ],
      }),
    }),
  );
  await page.route(`**/api/flow/${FLOW_ID}/advance`, (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );
  await page.route("**/api/session", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: SESSION_ID,
        mode: "targeted",
        first_question: "Which workflow orchestration tools have you run in production, and for what?",
        question: "Which workflow orchestration tools have you run in production, and for what?",
        estimated_questions: 5,
        hard_ceiling: 6,
        gaps_total: 3,
        gaps_remaining: 3,
        choices: null,
        resumed: false,
        current_gap_id: "c-orch",
        addressed_gap_ids: [],
        questions_asked: 1,
      }),
    }),
  );
  await page.route(`**/api/session/${SESSION_ID}/message`, (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(turn) }),
  );
}

/** A next-question turn (the interview continues). */
export function nextTurn(receipt: string | null): Record<string, unknown> {
  return {
    complete: false,
    question: "Have you built or run a streaming pipeline — Kafka, Flink or similar?",
    gaps_remaining: 2,
    choices: null,
    current_gap_id: "c-stream",
    addressed_gap_ids: ["c-orch"],
    changes_applied: false,
    denial_recorded: false,
    no_write_receipt: receipt,
  };
}

/** The final turn (completion screen). */
export function finalTurn(receipt: string | null): Record<string, unknown> {
  return {
    complete: true,
    reason: "max_questions_reached",
    questions_asked: 6,
    gaps_resolved: 1,
    completeness_score: 0.71,
    changes_applied: false,
    denial_recorded: false,
    no_write_receipt: receipt,
  };
}

/** Load the interview and send ANSWER. */
export async function answerOnce(page: Page): Promise<void> {
  // The completion screen mounts DecisionTrailReview; keep it quiet.
  await page.route("**/api/profile/enrichment-history**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.goto(`/flow/${FLOW_ID}/interview`);
  // #789 — a fresh session opens on the intro (why / how long / stop / honest).
  await page.getByTestId("interview-intro-start").click();
  await page.getByTestId("answer-textarea").fill(ANSWER);
  await page.getByTestId("send-button").click();
}
