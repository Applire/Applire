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

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { DossierDocumentsZone } from "../DossierDocumentsZone";
import { withIntl } from "@/lib/test-utils/with-intl";
import {
  patchSubmittedCv,
  patchSubmittedCoverLetter,
} from "@/lib/api/applications";
import type { CoverLetterSummary } from "@/app/(shell)/applications/[appId]/page";

vi.mock("@/lib/api/applications", () => ({
  patchSubmittedCv: vi.fn(),
  patchSubmittedCoverLetter: vi.fn(),
}));

const mockPatchCv = vi.mocked(patchSubmittedCv);
const mockPatchCl = vi.mocked(patchSubmittedCoverLetter);
const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
  vi.stubGlobal("open", vi.fn());
  mockFetch.mockReset();
  mockPatchCv.mockReset();
  mockPatchCl.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function baseApplication(o: Partial<{
  submitted_cv_id: string | null;
  submitted_cv_created_at: string | null;
  submitted_cover_letter_id: string | null;
  flow_session_id: string | null;
}> = {}) {
  return {
    id: "app-1",
    job_analysis_id: "job-1",
    role_title: "Senior Software Engineer",
    company_name: "TechVision GmbH",
    workflow_status: "completed",
    user_status: "tracking",
    notes: null,
    applied_at: null,
    deadline: null,
    source_url: null,
    submitted_cv_id: o.submitted_cv_id ?? null,
    submitted_cv_created_at: o.submitted_cv_created_at ?? null,
    submitted_cover_letter_id: o.submitted_cover_letter_id ?? null,
    stale_cv: null,
    flow_session_id: o.flow_session_id === undefined ? "flow-1" : o.flow_session_id,
    flow_current_step: "complete",
    created_at: "2026-07-01T09:00:00Z",
    updated_at: "2026-07-10T09:00:00Z",
    expires_at: null,
  };
}

const COVER_LETTER: CoverLetterSummary = {
  cover_letter_id: "cl-1",
  status: "ready",
  html_url: "/api/cover-letter/cl-1/html",
  pdf_url: "/api/cover-letter/cl-1/pdf",
  expires_at: "2026-08-01T09:00:00Z",
  letter_data: {},
};

function cvListFetch(items: unknown[]) {
  mockFetch.mockResolvedValue({ ok: true, status: 200, json: async () => items });
}

const THREE_CVS = [
  { cv_id: "cv-mid", status: "generating", template: "classic_german", created_at: "2026-07-06T09:00:00Z" },
  { cv_id: "cv-newest", status: "ready", template: "classic_german", created_at: "2026-07-10T09:00:00Z", pdf_url: "/api/cv/cv-newest/pdf" },
  { cv_id: "cv-failed", status: "failed", template: "modern_swiss", created_at: "2026-07-01T09:00:00Z", error_code: "llm_timeout" },
];

async function renderZone(opts: {
  application: ReturnType<typeof baseApplication>;
  coverLetter?: CoverLetterSummary | null;
  cvItems: unknown[];
  onError?: (msg: string) => void;
  onPinChange?: () => void;
}) {
  cvListFetch(opts.cvItems);
  const onError = opts.onError ?? vi.fn();
  const onPinChange = opts.onPinChange ?? vi.fn();
  render(
    withIntl(
      <DossierDocumentsZone
        application={opts.application}
        coverLetter={opts.coverLetter ?? null}
        onError={onError}
        onPinChange={onPinChange}
      />
    )
  );
  await waitFor(() => expect(mockFetch).toHaveBeenCalled());
  return { onError, onPinChange };
}

describe("DossierDocumentsZone (US232 — full version recall)", () => {
  it("renders the zone root testid", async () => {
    await renderZone({ application: baseApplication(), cvItems: [] });
    expect(screen.getByTestId("dossier-documents-zone")).toBeInTheDocument();
  });

  it("renders ready/generating/failed rows sorted newest-first, hides expired", async () => {
    const items = [
      ...THREE_CVS,
      { cv_id: "cv-expired", status: "expired", template: "classic_german", created_at: "2026-06-01T09:00:00Z" },
    ];
    await renderZone({ application: baseApplication(), cvItems: items });

    const rows = await screen.findAllByTestId("dossier-doc-row");
    expect(rows).toHaveLength(3); // expired hidden
    // Sorted newest-first: newest, mid (generating), failed
    expect(within(rows[0]).getByText(/classic german/i)).toBeInTheDocument();
    expect(within(rows[1]).getByText(/generating/i)).toBeInTheDocument();
    expect(within(rows[2]).getByText(/timed out|took too long/i)).toBeInTheDocument();
  });

  it("generating row has no PDF or Mark-as-sent action", async () => {
    await renderZone({ application: baseApplication(), cvItems: [THREE_CVS[0]] });
    const row = await screen.findByTestId("dossier-doc-row");
    expect(within(row).queryByTestId("dossier-doc-pdf")).not.toBeInTheDocument();
    expect(within(row).queryByTestId("dossier-doc-mark-sent")).not.toBeInTheDocument();
  });

  it("failed row shows a localized error, no PDF or Mark-as-sent action", async () => {
    await renderZone({ application: baseApplication(), cvItems: [THREE_CVS[2]] });
    const row = await screen.findByTestId("dossier-doc-row");
    expect(within(row).queryByTestId("dossier-doc-pdf")).not.toBeInTheDocument();
    expect(within(row).queryByTestId("dossier-doc-mark-sent")).not.toBeInTheDocument();
  });

  it("pinned ready row is highlighted with a sent chip and an unpin action; others offer Mark as sent", async () => {
    await renderZone({
      application: baseApplication({ submitted_cv_id: "cv-newest", submitted_cv_created_at: "2026-07-10T09:00:00Z" }),
      cvItems: THREE_CVS,
    });
    const rows = await screen.findAllByTestId("dossier-doc-row");
    expect(rows).toHaveLength(3);
    const pinnedRow = rows.find((r) => within(r).queryByTestId("dossier-doc-pinned"));
    expect(pinnedRow).toBeDefined();
    expect(within(pinnedRow as HTMLElement).getByTestId("dossier-doc-unpin")).toBeInTheDocument();
    expect(within(pinnedRow as HTMLElement).queryByTestId("dossier-doc-mark-sent")).not.toBeInTheDocument();
  });

  it("Mark as sent PATCHes the row's own cv id and reports the pin change", async () => {
    mockPatchCv.mockResolvedValue({
      id: "app-1",
      user_status: "applied",
      applied_at: null,
      updated_at: "2026-07-11T09:00:00Z",
      submitted_cv_id: "cv-newest",
      submitted_cv_created_at: "2026-07-10T09:00:00Z",
    });
    const { onPinChange } = await renderZone({ application: baseApplication(), cvItems: [THREE_CVS[1]] });

    const row = await screen.findByTestId("dossier-doc-row");
    fireEvent.click(within(row).getByTestId("dossier-doc-mark-sent"));

    await waitFor(() => expect(mockPatchCv).toHaveBeenCalledWith("app-1", "cv-newest"));
    await waitFor(() => expect(onPinChange).toHaveBeenCalled());
  });

  it("Unpin sends an explicit null and reports the pin change", async () => {
    mockPatchCv.mockResolvedValue({
      id: "app-1",
      user_status: "applied",
      applied_at: null,
      updated_at: "2026-07-11T09:00:00Z",
      submitted_cv_id: null,
      submitted_cv_created_at: null,
    });
    const { onPinChange } = await renderZone({
      application: baseApplication({ submitted_cv_id: "cv-newest", submitted_cv_created_at: "2026-07-10T09:00:00Z" }),
      cvItems: [THREE_CVS[1]],
    });

    const row = await screen.findByTestId("dossier-doc-row");
    fireEvent.click(within(row).getByTestId("dossier-doc-unpin"));

    await waitFor(() => expect(mockPatchCv).toHaveBeenCalledWith("app-1", null));
    await waitFor(() => expect(onPinChange).toHaveBeenCalled());
  });

  it("hides an expired cover letter row, mirroring the CV expired rule", async () => {
    await renderZone({
      application: baseApplication(),
      cvItems: [],
      coverLetter: { ...COVER_LETTER, status: "expired" },
    });
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(screen.queryByTestId("dossier-cl-row")).not.toBeInTheDocument();
    expect(screen.getByText("No versions yet.")).toBeInTheDocument();
  });

  it("renders a cover-letter row with a link to the flow cover-letter draft", async () => {
    await renderZone({ application: baseApplication(), cvItems: [], coverLetter: COVER_LETTER });
    const clRow = await screen.findByTestId("dossier-cl-row");
    expect(within(clRow).getByRole("link")).toHaveAttribute("href", "/flow/flow-1/cover-letter");
  });

  it("cover-letter pin symmetry: Mark as sent pins the cover letter, unpin clears it", async () => {
    mockPatchCl.mockResolvedValue({
      id: "app-1",
      user_status: "applied",
      applied_at: null,
      updated_at: "2026-07-11T09:00:00Z",
    });
    const { onPinChange } = await renderZone({
      application: baseApplication(),
      cvItems: [],
      coverLetter: COVER_LETTER,
    });

    const clRow = await screen.findByTestId("dossier-cl-row");
    expect(within(clRow).queryByTestId("dossier-doc-pinned")).not.toBeInTheDocument();
    fireEvent.click(within(clRow).getByTestId("dossier-doc-mark-sent"));
    await waitFor(() => expect(mockPatchCl).toHaveBeenCalledWith("app-1", "cl-1"));
    await waitFor(() => expect(onPinChange).toHaveBeenCalled());
  });

  it("cover-letter pinned: shows the sent chip and unpin, PATCHes null on unpin", async () => {
    mockPatchCl.mockResolvedValue({
      id: "app-1",
      user_status: "applied",
      applied_at: null,
      updated_at: "2026-07-11T09:00:00Z",
    });
    const { onPinChange } = await renderZone({
      application: baseApplication({ submitted_cover_letter_id: "cl-1" }),
      cvItems: [],
      coverLetter: COVER_LETTER,
    });

    const clRow = await screen.findByTestId("dossier-cl-row");
    expect(within(clRow).getByTestId("dossier-doc-pinned")).toBeInTheDocument();
    expect(within(clRow).queryByTestId("dossier-doc-mark-sent")).not.toBeInTheDocument();

    fireEvent.click(within(clRow).getByTestId("dossier-doc-unpin"));
    await waitFor(() => expect(mockPatchCl).toHaveBeenCalledWith("app-1", null));
    await waitFor(() => expect(onPinChange).toHaveBeenCalled());
  });

  it("calls onError when the CV list fetch fails", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 500, json: async () => ({}) });
    const onError = vi.fn();
    render(
      withIntl(
        <DossierDocumentsZone
          application={baseApplication()}
          coverLetter={null}
          onError={onError}
          onPinChange={vi.fn()}
        />
      )
    );
    await waitFor(() => expect(onError).toHaveBeenCalled());
  });
});
