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

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { GenerateCoverLetterModal } from "../GenerateCoverLetterModal";

const BASE_PROPS = {
  jobId: "job-1",
  onClose: vi.fn(),
  onGenerated: vi.fn(),
};

function renderModal() {
  return render(withIntl(<GenerateCoverLetterModal {...BASE_PROPS} />, "de"));
}

describe("GenerateCoverLetterModal", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // #311: the failure path threw new Error("Generation failed"), and because
  // that is always an Error instance, err.message displaced the
  // t("errorGenerationFailed") fallback sitting right beside it — English text
  // in the German modal.
  it("shows the translated error when the backend sends no detail", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({}),
    } as Response);

    renderModal();
    fireEvent.click(screen.getByTestId("cl-modal-generate"));

    await waitFor(() => expect(screen.getByText("Unbekannter Fehler")).toBeTruthy());
    expect(screen.queryByText("Generation failed")).toBeNull();
  });

  it("still prefers a deliberately worded backend detail", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ detail: "Profil unvollständig." }),
    } as Response);

    renderModal();
    fireEvent.click(screen.getByTestId("cl-modal-generate"));

    await waitFor(() => expect(screen.getByText("Profil unvollständig.")).toBeTruthy());
  });

  it("renders the tonality options in German", () => {
    renderModal();
    expect(screen.getByText("Förmlich")).toBeTruthy();
    expect(screen.getByText("Professionell")).toBeTruthy();
    expect(screen.getByText("Locker")).toBeTruthy();
  });
});

// Frontend collector #604 — "the cover-letter creation modal leaves the recipient
// company blank even though the field is labelled '(aus Stellenanzeige)' and
// job_analyses.company_name is already known". A confidence gap, not a functional
// one: the backend re-derives the company independently, so the letter is right
// while the form looks ignorant.
//
// The mechanism already existed and no caller fed it — `prefillRecipientCompany`
// is declared on the props and is passed by none of the three call sites
// (cv/page.tsx, cover-letter/page.tsx x2). Fixing it at each call site would need
// each page to fetch the job analysis; fixing it HERE fixes all three at once,
// and `GET /api/job/{id}` (JobAnalysisResponse.company_name) is the endpoint that
// actually carries the field — the flow state's JobAnalysisSummary does NOT, it
// has only job_id and role_title.
describe("GenerateCoverLetterModal — recipient company prefill (#604)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function jobResponse(company: string | null) {
    return {
      ok: true,
      json: () => Promise.resolve({ id: "job-1", role_title: "Senior Engineer", company_name: company }),
    } as Response;
  }

  it("fills the company from the job analysis", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jobResponse("Rheinwerk Verpackungen GmbH"));
    renderModal();
    await waitFor(() =>
      expect(screen.getByTestId("cl-recipient-company")).toHaveValue("Rheinwerk Verpackungen GmbH"),
    );
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/job/job-1"));
  });

  it("does not fetch when a caller already supplied the company", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue(jobResponse("Von der API"));
    render(
      withIntl(
        <GenerateCoverLetterModal {...BASE_PROPS} prefillRecipientCompany="Vom Aufrufer GmbH" />,
        "de",
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("cl-recipient-company")).toHaveValue("Vom Aufrufer GmbH"),
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("does not overwrite a regenerate's stored recipient company", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue(jobResponse("Von der API"));
    render(
      withIntl(
        <GenerateCoverLetterModal
          {...BASE_PROPS}
          existingInputs={{ recipient_company: "Beim letzten Mal GmbH" }}
        />,
        "de",
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("cl-recipient-company")).toHaveValue("Beim letzten Mal GmbH"),
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("leaves the field empty when the analysis knows no company", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jobResponse(null));
    renderModal();
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.getByTestId("cl-recipient-company")).toHaveValue("");
  });

  it("stays usable when the lookup fails", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new Error("network"));
    renderModal();
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.getByTestId("cl-recipient-company")).toHaveValue("");
    expect(screen.queryByText("Unbekannter Fehler")).toBeNull();
  });

  it("never clobbers what the user has already typed", async () => {
    // The lookup is async; a fast typist must win it.
    let resolve!: (v: Response) => void;
    vi.spyOn(global, "fetch").mockReturnValue(
      new Promise<Response>((r) => {
        resolve = r;
      }) as ReturnType<typeof global.fetch>,
    );
    renderModal();
    const field = screen.getByTestId("cl-recipient-company");
    fireEvent.change(field, { target: { value: "Selbst getippt AG" } });
    resolve(jobResponse("Rheinwerk Verpackungen GmbH"));
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(field).toHaveValue("Selbst getippt AG");
  });
});
