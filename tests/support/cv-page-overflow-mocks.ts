// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #786 L-3: route mocks shared by the desktop and mobile OQ lanes of the x+1 card.
// page.route() only — no running backend.
import type { Page } from "@playwright/test";

export const PO_FLOW_ID = "flow-pageoverflow-0000-0000-000000000001";
export const PO_CV_ID = "cv-pageoverflow-0000-0000-0000-000000000002";
const JOB_ID = "job-pageoverflow-0000-0000-0000-00000003";

export const PROPOSAL = {
  target: 2,
  actual: 3,
  proposed: [
    { kind: "oldest_certification", items: ["REFA-Grundschein (2013)", "Sicherheitsbeauftragter (2014)"] },
    { kind: "off_job_skills", items: ["Kosmetik-Verpackungen", "Lebensmittelrecht"] },
  ],
  choice: null as null | "keep" | "cut",
  cut_available: true,
  uncut_available: false,
};

export async function setupPageOverflowMocks(page: Page): Promise<{ posts: unknown[] }> {
  const posts: unknown[] = [];
  let current = { ...PROPOSAL };
  await page.route(`**/api/flow/${PO_FLOW_ID}/state`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: JOB_ID,
        job_summary: { role_title: "Leiter Qualitätssicherung" },
        gap_summary: { match_score: 0.82, gaps: [], sections: [] },
        cv_summary: {
          cv_id: PO_CV_ID,
          pdf_url: `/api/cv/${PO_CV_ID}/pdf`,
          expires_at: new Date(Date.now() + 86_400_000).toISOString(),
        },
      }),
    }),
  );
  await page.route(`**/api/cv/${PO_CV_ID}/html`, (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<html><body><h1>Jörg Müller</h1></body></html>" }),
  );
  await page.route(`**/api/cv/${PO_CV_ID}/status`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        cv_id: PO_CV_ID,
        status: "ready",
        expires_at: new Date(Date.now() + 86_400_000).toISOString(),
        template: "classic_german",
        document_language: "de",
        target_pages: 2,
        page_overflow: current,
      }),
    }),
  );
  await page.route(`**/api/cv/${PO_CV_ID}/page-overflow`, async (route) => {
    const body = route.request().postDataJSON() as { choice: "keep" | "cut" };
    posts.push(body);
    current = {
      ...current,
      choice: body.choice,
      cut_available: body.choice !== "cut",
      uncut_available: body.choice === "cut",
    };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(current) });
  });
  await page.route(`**/api/cv/${PO_CV_ID}/ats-report`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ report: null }) }),
  );
  await page.route(`**/api/cv/${PO_CV_ID}/sections`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ sections: [], general_gaps: [] }) }),
  );
  await page.route(`**/api/cv/${PO_CV_ID}/profile-diff`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], grounded: true }) }),
  );
  return { posts };
}
