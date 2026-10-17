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
 * #763 (ruling ADV-1) — page.route stubs for the /gaps composers' no-write
 * receipt (desktop and mobile OQ). Synthetic data; no backend is reached.
 */
import type { Page } from "@playwright/test";

export const FLOW_ID = "f7630000-0000-0000-0000-0000000000c1";
const JOB_ID = "f7630000-0000-0000-0000-0000000000c2";
const GAP_ID = "f7630000-0000-0000-0000-0000000000c3";
export const CLUSTER = "cl-k8s";
export const LIABILITY = "Kubernetes";
export const OFF_TOPIC = "Last year I was on holiday in Portugal and read a lot of books.";

const ROW = {
  id: GAP_ID,
  match_score: 0.6,
  category_a: ["Python"],
  category_b: [],
  category_c: ["Kubernetes", "Helm"],
  strengths: ["Python"],
  keyword_liabilities: [{ concept: LIABILITY, evidence: "listed under Skills" }],
  gap_clusters: [
    {
      id: CLUSTER,
      label: "Container Orchestration",
      category: "C",
      gaps: ["Kubernetes", "Helm"],
      jd_skills: ["Kubernetes"],
      jd_context: "",
      outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
      coverage: "open",
      budget_remaining: 2,
      member_statuses: [
        { member: "Kubernetes", status: "gap" },
        { member: "Helm", status: "gap" },
      ],
    },
  ],
};

const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

/** Stub /gaps; every micro-session answer returns `turn`. `lang` is stated
 * explicitly — CI's harness answers its own saved language otherwise. */
export async function stubGaps(page: Page, turn: Record<string, unknown>, lang: "en" | "de" = "en") {
  await page.route(`**/api/flow/${FLOW_ID}/state`, (r) =>
    r.fulfill(json({ job_id: JOB_ID, current_step: "gap_analysis", user_type: "new", available_actions: {},
      gap_summary: { gap_analysis_id: GAP_ID }, job_summary: { role_title: "Platform Engineer" } })),
  );
  await page.route(`**/api/job/${JOB_ID}/gaps`, (r) => r.fulfill(json(ROW)));
  await page.route(`**/api/job/${JOB_ID}/gaps/refresh`, (r) => r.fulfill(json(ROW)));
  await page.route("**/api/profile", (r) =>
    r.fulfill(json({ positions_count: 2, projects_count: 1, certifications_count: 0, data_points_count: 9 })),
  );
  await page.route("**/api/profile/health", (r) => r.fulfill(json({ issues: [] })));
  await page.route("**/api/settings", (r) =>
    r.fulfill(json({ ui_language: lang, ui_language_explicit: true, dismissed_explainers: [] })),
  );
  await page.route("**/api/session", (r) =>
    r.fulfill(json({ session_id: "micro-763", question: "Tell me how you ran Kubernetes.", choices: null })),
  );
  await page.route("**/api/session/micro-763/message", (r) => r.fulfill(json(turn)));
}

export const doneTurn = (receipt: string | null, wrote = false) => ({
  complete: true,
  reason: "max_questions_reached",
  changes_applied: wrote,
  no_write_receipt: receipt,
});
