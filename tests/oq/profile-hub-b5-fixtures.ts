// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Hazelnut build 5 (UAT-1, U-2, U-9, U-11, UAT-3d; ADR-099 cl. 10/11) — shared
// network stubs for the profile hub specs (desktop + mobile lane). Synthetic
// data only. Drives the real profile page, the real review drawer and the real
// merge dialog through the real catalogs; mocks only the network boundary and
// records every request body the UI sends, so a spec asserts on the WIRE
// (structured outcome, pair order), not on a component's internal state.

import { expect, type Page } from "@playwright/test";

export const PROFILE = {
  id: "profile-1",
  profile: {
    personal_info: { name: "Lena Fischer" },
    work_experience: [
      {
        id: "we-1",
        role: "Quality Lead",
        company: "Nordwerk Logistik GmbH",
        start_date: "2015-03",
        end_date: "2019-06",
        achievements: ["Rebuilt supplier selection for 4 sites and cut scrap by 18 %."],
      },
      {
        id: "we-2",
        role: "Qualitätsleiter",
        company: "Nordwerk Logistik",
        start_date: "2015-03",
        end_date: "2019-06",
        achievements: ["Lieferantenauswahl für 4 Standorte neu aufgebaut."],
      },
    ],
    education: [],
    skills: [],
    languages: [],
    projects: [],
  },
  completeness: 0.8,
  gaps: [],
  merge_conflicts: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

export const HEALTH = {
  issues: [
    {
      id: "conflict:c-list",
      thread: "conflict",
      delivery_class: "A",
      profile_mismatch_severity: "review",
      summary: "Quality Lead @ Nordwerk Logistik GmbH: achievements",
      entity_label: "Quality Lead @ Nordwerk Logistik GmbH",
      section: "work_experience",
      field: "achievements",
      existing_value_display: "Rebuilt supplier selection for 4 sites and cut scrap by 18 %.",
      incoming_value_display: "Introduced a batch-release quality dashboard across 4 sites.",
      incoming_source: "cv_upload",
    },
    {
      id: "accuracy:1",
      thread: "accuracy",
      delivery_class: "S",
      profile_mismatch_severity: "review",
      summary: "Datenabgleich: summary names 12 years, roles add up to 9",
    },
    {
      id: "duplicate_entry:we-1:we-2",
      thread: "duplicate_entry",
      delivery_class: "U",
      profile_mismatch_severity: "review",
      summary: "same station?",
      section: "work_experience",
      entry_ids: ["we-1", "we-2"],
      entity_label: "Quality Lead @ Nordwerk Logistik GmbH",
      other_entity_label: "Qualitätsleiter @ Nordwerk Logistik",
    },
  ],
  completeness: { score: 0.8, gaps: ["education"], field_gaps: [] },
};

export const LIST_CONFLICT = {
  conflict_id: "c-list",
  shape: "list",
  outcomes: ["keep_current", "take_new", "keep_both", "merge"],
  entity_label: "Quality Lead @ Nordwerk Logistik GmbH",
  section: "work_experience",
  field: "achievements",
  existing_value_display: "Rebuilt supplier selection for 4 sites and cut scrap by 18 %.",
  incoming_value_display: "Introduced a batch-release quality dashboard across 4 sites.",
  incoming_source: "cv_upload",
};

export const PREVIEW = {
  merged_entry: {},
  aliases: [{ role: "Qualitätsleiter", company: "Nordwerk Logistik" }],
  date_conflict: null,
  duplicate_pairs: [
    {
      pair_id: "p1",
      target_text: "Rebuilt supplier selection for 4 sites and cut scrap by 18 %.",
      source_text: "Lieferantenauswahl für 4 Standorte neu aufgebaut.",
      proposal: null,
    },
  ],
  unique_bullets: [],
};

export interface Wire {
  url: string;
  body: unknown;
}

export async function openHub(page: Page): Promise<Wire[]> {
  const wire: Wire[] = [];
  const record = (url: string, body: string | null) =>
    wire.push({ url, body: body ? JSON.parse(body) : null });
  const json = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/api/profile", (route) => route.fulfill(json(PROFILE)));
  await page.route("**/api/profile/enrichment-history", (route) => route.fulfill(json([])));
  await page.route("**/api/profile/import-summary", (route) => route.fulfill(json({ record_id: null })));
  await page.route("**/api/profile/health", (route) => route.fulfill(json(HEALTH)));
  await page.route("**/api/session/profile-review", (route) => {
    record(route.request().url(), route.request().postData());
    return route.fulfill(
      json({
        session_id: "s1",
        first_question: "Two values — which is right?",
        gaps_total: 1,
        gaps_remaining: 1,
        choices: null,
        current_conflict: LIST_CONFLICT,
        follow_up_used: false,
      }),
    );
  });
  await page.route("**/api/session/s1/message", (route) => {
    record(route.request().url(), route.request().postData());
    return route.fulfill(
      json({ complete: true, question: null, choices: null, gaps_remaining: 0, current_conflict: null }),
    );
  });
  await page.route("**/api/profile/entries/merge", (route) => {
    record(route.request().url(), route.request().postData());
    return route.fulfill(json(PREVIEW));
  });
  await page.route("**/api/profile/entries/keep-separate", (route) => {
    record(route.request().url(), route.request().postData());
    return route.fulfill(json({}));
  });
  await page.goto("/profile");
  await expect(page.getByTestId("health-panel")).toBeVisible({ timeout: 30000 });
  return wire;
}

export async function shot(page: Page, name: string) {
  const dir = process.env.A2_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: false });
}
