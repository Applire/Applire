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
 * D-6 (founder replay 2026-10-28, Pixel 7): the preview's page bands follow the
 * PDF at an entry's head. The PDF engine (Chromium print) moves a whole
 * date-beside-entry row when the entry's head does not fit; the preview left
 * "05/2021 – 11/2024" alone at the foot of page 1. Measured on the seven real
 * templates against their printed PDFs (3360 entries, 0 date/title splits in
 * the PDF); this spec pins the shape with fixed-height blocks so it does not
 * depend on the machine's fonts.
 * D-6 residual (delivery run 4): a heading the template keeps with what follows
 * (`break-after: avoid`) moves with its first block — "PROJECTS" ended page 1.
 */
import { test, expect } from '../support/auth-fixture';

const FLOW_ID = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
const CV_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc';
const JOB_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';

// A4 as the PDF engine sizes it (842.88 pt) minus the 20 mm @page margins.
const A4_PX = (842.88 * 4) / 3;
const MARGIN_PX = (20 * 96) / 25.4;
const CONTENT_PX = A4_PX - 2 * MARGIN_PX;

function cvHtml(fillerPx: number, leadPx: number): string {
  return `<!doctype html><html><head><style>
    @page { size: A4; margin: 20mm; }
    body { margin: 0; font: 13px/16px Arial, sans-serif; }
    @media screen { .page { padding: 20mm; } }
    .filler { height: ${fillerPx}px; }
    .entry { display: flex; gap: 12px; }
    .entry-dates { width: 130px; flex: none; }
    .entry-body { flex: 1; }
    .entry-lead { height: ${leadPx}px; break-inside: avoid; }
    .entry-body li { height: 40px; break-inside: avoid; }
  </style></head><body><div class="page">
    <div class="filler"></div>
    <div class="entry">
      <div class="entry-dates" data-testid="d6-date">05/2021&nbsp;–&nbsp;11/2024</div>
      <div class="entry-body">
        <div class="entry-lead"><div class="entry-title" data-testid="d6-title">Schichtleiterin</div></div>
        <ul><li>a</li><li>b</li><li>c</li><li>d</li><li>e</li></ul>
      </div>
    </div>
  </div></body></html>`;
}

/** D-6 residual (delivery run 4): a heading kept with its next block, then a block that does not fit. */
function headingHtml(fillerPx: number, nested: boolean): string {
  const block = `<div class="item" data-testid="d6-item"><div class="name">Generative AI Automation Platform</div><div class="body"></div></div>`;
  return `<!doctype html><html><head><style>
    @page { size: A4; margin: 20mm; }
    body { margin: 0; font: 13px/16px Arial, sans-serif; }
    @media screen { .page { padding: 20mm; } }
    .filler { height: ${fillerPx}px; }
    .section-title, .entry-projects-title { height: 20px; break-after: avoid; page-break-after: avoid; }
    .item { break-inside: avoid; }
    .item .name { height: 20px; }
    .item .body { height: 100px; }
  </style></head><body><div class="page">
    <div class="filler"></div>
    ${nested
      ? `<div class="entry-projects"><div class="entry-projects-title" data-testid="d6-heading">PROJECTS</div>${block}</div>`
      : `<section><h2 class="section-title" data-testid="d6-heading">PROJECTS</h2>${block}</section>`}
  </div></body></html>`;
}

/**
 * D-35 (delivery run 8): a role whose nested projects make it long. Head + 2 own
 * bullets keep together (.entry-lead); the projects never open a page on their
 * own (.entry-projects break-before: avoid); a project keeps its name + first
 * bullet together (.project-lead) and may break after it. Fixed heights.
 */
function nestedProjectsHtml(fillerPx: number): string {
  const li = (t: string) => `<li>${t}</li>`;
  return `<!doctype html><html><head><style>
    @page { size: A4; margin: 20mm; }
    body { margin: 0; font: 13px/16px Arial, sans-serif; }
    @media screen { .page { padding: 20mm; } }
    ul { margin: 0; padding: 0 0 0 16px; }
    .filler { height: ${fillerPx}px; }
    .entry { display: flex; gap: 12px; }
    .entry-dates { width: 130px; flex: none; }
    .entry-body { flex: 1; }
    .entry-title, .project-name, .entry-projects-title { height: 20px; }
    li { height: 40px; break-inside: avoid; }
    .entry-lead, .project-lead { break-inside: avoid; }
    .entry-projects { break-before: avoid; }
    .entry-projects-title { break-after: avoid; }
  </style></head><body><div class="page">
    <div class="filler"></div>
    <div class="entry">
      <div class="entry-dates">05/2021&nbsp;–&nbsp;11/2024</div>
      <div class="entry-body">
        <div class="entry-lead"><div class="entry-title" data-testid="d35-role">Schichtleiterin</div><ul>${li('a')}${li('b')}</ul></div>
        <div class="entry-projects">
          <div class="entry-projects-title">PROJEKTE</div>
          <div class="project-item">
            <div class="project-lead"><div class="project-name" data-testid="d35-project">Vorhaben</div><ul>${li('p1')}</ul></div>
            <ul><li>p2</li><li>p3</li><li data-testid="d35-p4">p4</li><li>p5</li></ul>
          </div>
        </div>
      </div>
    </div>
  </div></body></html>`;
}

async function open(page: import('@playwright/test').Page, html: string) {
  await page.route(`**/api/flow/${FLOW_ID}/state`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: JOB_ID, job_summary: { role_title: 'Schichtleitung' }, cv_summary: { cv_id: CV_ID } }),
    }),
  );
  await page.route(`**/api/cv/${CV_ID}/html`, (route) => route.fulfill({ status: 200, contentType: 'text/html', body: html }));
  await page.goto(`/flow/${FLOW_ID}/cv`);
  const frame = page.frameLocator('iframe[data-testid="cv-iframe"]');
  await expect(frame.locator('[data-applire-page-break="band"]').first()).toBeAttached({ timeout: 30_000 });
  return frame;
}

/** Page index (1-based) of an element in the paginated preview, from the bands above it. */
async function pageOf(frame: import('@playwright/test').FrameLocator, testId: string): Promise<number> {
  return frame.getByTestId(testId).evaluate((el) => {
    const top = el.getBoundingClientRect().top;
    const bands = Array.from(el.ownerDocument.querySelectorAll('[data-applire-page-break="band"]'));
    return bands.filter((b) => b.getBoundingClientRect().top < top).length + 1;
  });
}

test.describe('CV preview page bands — D-6 parity with the PDF', () => {
  test('an entry whose head does not fit moves WITH its date (the row moves, as in the PDF)', async ({ page }) => {
    // The head starts 60 px above the edge and is 120 px tall; the date fits on its own.
    const frame = await open(page, cvHtml(CONTENT_PX - 60, 120));
    expect(await pageOf(frame, 'd6-title')).toBe(2);
    expect(await pageOf(frame, 'd6-date')).toBe(2);
  });

  test('a head ending a fraction of a pixel inside the edge moves, as the print layout moves it', async ({ page }) => {
    const frame = await open(page, cvHtml(CONTENT_PX - 120, 119.5));
    expect(await pageOf(frame, 'd6-title')).toBe(2);
    expect(await pageOf(frame, 'd6-date')).toBe(2);
  });

  for (const nested of [false, true]) {
    test(`a ${nested ? 'nested "Projekte" sub-label' : 'section heading'} never ends a page alone — it moves with its first block`, async ({ page }) => {
      // The heading fits 60 px above the edge; the 120 px block after it does not.
      const frame = await open(page, headingHtml(CONTENT_PX - 60, nested));
      expect(await pageOf(frame, 'd6-item')).toBe(2);
      expect(await pageOf(frame, 'd6-heading')).toBe(2);
    });
  }

  test('D-35: nested projects never open a page without their role — the role moves with them', async ({ page }) => {
    // The project's lead (name + first bullet) crosses the edge: it, the label and the role's lead go together.
    const frame = await open(page, nestedProjectsHtml(CONTENT_PX - 150));
    expect(await pageOf(frame, 'd35-project')).toBe(2);
    expect(await pageOf(frame, 'd35-role')).toBe(2);
  });

  test('D-35: a long nested project breaks after its lead — the role does not move whole', async ({ page }) => {
    // The project's 4th bullet crosses the edge: only it and what follows go to page 2.
    const frame = await open(page, nestedProjectsHtml(CONTENT_PX - 240));
    expect(await pageOf(frame, 'd35-role')).toBe(1);
    expect(await pageOf(frame, 'd35-project')).toBe(1);
    expect(await pageOf(frame, 'd35-p4')).toBe(2);
  });
});
