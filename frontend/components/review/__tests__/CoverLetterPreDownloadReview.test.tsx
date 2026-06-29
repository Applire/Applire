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

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

vi.mock("next-intl", () => ({ useTranslations: () => Object.assign((key: string) => key, { has: () => true }) }));

import { CoverLetterPreDownloadReview } from "../CoverLetterPreDownloadReview";

describe("CoverLetterPreDownloadReview", () => {
  it("renders the download-mode attestation surface (US170 / ADR-040 §3)", () => {
    render(<CoverLetterPreDownloadReview onAttested={vi.fn()} />);
    expect(screen.getByTestId("what-changed-review")).toHaveAttribute("data-mode", "download");
    // attestation-only: a cover letter has no deterministic field diff, so the empty
    // state shows and the attestation note + confirm appear.
    expect(screen.getByTestId("what-changed-confirm")).toBeInTheDocument();
  });

  it("calls onAttested when the attestation confirm is clicked (nudge, not gate)", () => {
    const onAttested = vi.fn();
    render(<CoverLetterPreDownloadReview onAttested={onAttested} />);
    fireEvent.click(screen.getByTestId("what-changed-confirm"));
    expect(onAttested).toHaveBeenCalledOnce();
  });

  it("calls onDismiss when skipped — the flow is never blocked (ADR-040 §4)", () => {
    const onDismiss = vi.fn();
    render(<CoverLetterPreDownloadReview onAttested={vi.fn()} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByTestId("what-changed-skip"));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
