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

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));

const getCvProfileDiff = vi.fn();
vi.mock("@/lib/api/review", () => ({ getCvProfileDiff: (id: string) => getCvProfileDiff(id) }));

import { PreDownloadReview } from "../PreDownloadReview";

describe("PreDownloadReview", () => {
  beforeEach(() => getCvProfileDiff.mockReset());

  it("fetches the diff for the cv and renders the download-mode surface", async () => {
    getCvProfileDiff.mockResolvedValue({
      grounded: false,
      items: [{ section: "skills", field: "skills", action: "added", newValue: "Rust", rationale: "Not in your profile." }],
    });
    render(<PreDownloadReview cvId="cv-1" onAttested={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("what-changed-review")).toHaveAttribute("data-mode", "download"));
    expect(getCvProfileDiff).toHaveBeenCalledWith("cv-1");
    expect(screen.getByText("Not in your profile.")).toBeInTheDocument();
  });

  it("calls onAttested when the attestation confirm is clicked (nudge, not gate)", async () => {
    const onAttested = vi.fn();
    getCvProfileDiff.mockResolvedValue({ grounded: true, items: [] });
    render(<PreDownloadReview cvId="cv-1" onAttested={onAttested} />);
    await waitFor(() => screen.getByTestId("what-changed-confirm"));
    fireEvent.click(screen.getByTestId("what-changed-confirm"));
    expect(onAttested).toHaveBeenCalledOnce();
  });
});
