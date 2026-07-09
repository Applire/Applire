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
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SubmittedDocumentsCard } from "../SubmittedDocumentsCard";
import { patchSubmittedCv } from "@/lib/api/applications";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(",")}` : key,
}));

vi.mock("@/lib/api/applications", () => ({
  patchSubmittedCv: vi.fn(),
}));

const mockPatchPin = vi.mocked(patchSubmittedCv);
const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
  vi.stubGlobal("open", vi.fn());
  mockFetch.mockReset();
  mockPatchPin.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const BASE = {
  applicationId: "app-1",
  jobAnalysisId: "job-1",
  submittedCvId: null as string | null,
  submittedCvCreatedAt: null as string | null,
  submittedCoverLetterId: null as string | null,
};

function cvList(items: Array<{ cv_id: string; status: string }>) {
  mockFetch.mockResolvedValue({ ok: true, json: async () => items });
}

describe("SubmittedDocumentsCard (E039/US219 — Branch G recall)", () => {
  it("pinned: shows the submitted version with its date and opens the PDF", async () => {
    render(
      <SubmittedDocumentsCard
        {...BASE}
        submittedCvId="cv-9"
        submittedCvCreatedAt="2026-07-05T10:00:00Z"
      />,
    );

    expect(screen.getByTestId("submitted-doc-pinned")).toBeInTheDocument();
    // Version identity = the pin's creation date
    expect(
      screen.getByText((text) => text.startsWith("versionFrom:")),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("submitted-doc-open-pdf"));
    expect(window.open).toHaveBeenCalledWith(
      expect.stringContaining("/api/cv/cv-9/pdf"),
      "_blank",
      "noopener,noreferrer",
    );
    // The pinned path never needs the CV list
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("pinned: unpin sends an explicit null and flips to the fallback", async () => {
    cvList([{ cv_id: "cv-9", status: "ready" }]);
    mockPatchPin.mockResolvedValue({
      id: "app-1",
      user_status: "applied",
      applied_at: null,
      updated_at: "2026-07-08T10:00:00Z",
      submitted_cv_id: null,
      submitted_cv_created_at: null,
    });

    render(
      <SubmittedDocumentsCard
        {...BASE}
        submittedCvId="cv-9"
        submittedCvCreatedAt="2026-07-05T10:00:00Z"
      />,
    );

    fireEvent.click(screen.getByTestId("submitted-doc-unpin"));

    await waitFor(() => expect(mockPatchPin).toHaveBeenCalledWith("app-1", null));
    await waitFor(() =>
      expect(screen.getByTestId("submitted-doc-fallback")).toBeInTheDocument(),
    );
  });

  it("unpinned (Branch G): shows the last generated version with the honest label and pins it on demand", async () => {
    cvList([
      { cv_id: "cv-new", status: "ready" },
      { cv_id: "cv-old", status: "ready" },
    ]);
    mockPatchPin.mockResolvedValue({
      id: "app-1",
      user_status: "applied",
      applied_at: null,
      updated_at: "2026-07-08T10:00:00Z",
      submitted_cv_id: "cv-new",
      submitted_cv_created_at: "2026-07-07T09:00:00Z",
    });

    render(<SubmittedDocumentsCard {...BASE} />);

    // Honest fallback label, never a confident wrong answer
    const fallback = await screen.findByTestId("submitted-doc-fallback");
    expect(fallback).toHaveTextContent("lastGeneratedNotMarked");

    // Opens the newest ready CV
    fireEvent.click(screen.getByTestId("submitted-doc-open-pdf"));
    expect(window.open).toHaveBeenCalledWith(
      expect.stringContaining("/api/cv/cv-new/pdf"),
      "_blank",
      "noopener,noreferrer",
    );

    // Mark as submitted from the detail view pins the newest ready version
    fireEvent.click(screen.getByTestId("submitted-doc-mark"));
    await waitFor(() => expect(mockPatchPin).toHaveBeenCalledWith("app-1", "cv-new"));
    await waitFor(() =>
      expect(screen.getByTestId("submitted-doc-pinned")).toBeInTheDocument(),
    );
  });

  it("unpinned: skips non-ready CVs when picking the fallback", async () => {
    cvList([
      { cv_id: "cv-pending", status: "pending" },
      { cv_id: "cv-ready", status: "ready" },
    ]);

    render(<SubmittedDocumentsCard {...BASE} />);

    await screen.findByTestId("submitted-doc-fallback");
    fireEvent.click(screen.getByTestId("submitted-doc-open-pdf"));
    expect(window.open).toHaveBeenCalledWith(
      expect.stringContaining("/api/cv/cv-ready/pdf"),
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("renders nothing when there is no pin and no generated CV", async () => {
    cvList([]);

    const { container } = render(<SubmittedDocumentsCard {...BASE} />);

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("shows the pinned cover letter row and opens its PDF", () => {
    render(
      <SubmittedDocumentsCard
        {...BASE}
        submittedCvId="cv-9"
        submittedCvCreatedAt="2026-07-05T10:00:00Z"
        submittedCoverLetterId="cl-3"
      />,
    );

    expect(screen.getByText("coverLetterSubmitted")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("submitted-doc-open-cl-pdf"));
    expect(window.open).toHaveBeenCalledWith(
      expect.stringContaining("/api/cover-letter/cl-3/pdf"),
      "_blank",
      "noopener,noreferrer",
    );
  });
});
