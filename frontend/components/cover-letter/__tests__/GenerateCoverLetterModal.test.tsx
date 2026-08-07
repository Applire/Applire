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
