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
import { WhatChangedReview, type ReviewChange } from "../WhatChangedReview";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

const CHANGES: ReviewChange[] = [
  {
    section: "work_experience",
    field: "work_experience",
    action: "merged",
    newValue: "Senior Dev @ Acme",
    rationale: "Treated as the same position at Acme.",
  },
  {
    section: "skills",
    field: "skills",
    action: "added",
    newValue: "Kubernetes",
    rationale: "New skill from this source.",
  },
];

describe("WhatChangedReview", () => {
  it("renders the per-mode title", () => {
    render(<WhatChangedReview mode="extraction" changes={CHANGES} />);
    expect(screen.getByTestId("what-changed-title")).toHaveTextContent("titleExtraction");
  });

  it("renders one row per change with its rationale", () => {
    render(<WhatChangedReview mode="merge" changes={CHANGES} />);
    const rows = screen.getAllByTestId("what-changed-row");
    expect(rows).toHaveLength(2);
    expect(screen.getByText("Treated as the same position at Acme.")).toBeInTheDocument();
    expect(screen.getByText("New skill from this source.")).toBeInTheDocument();
  });

  it("calls onConfirm when the confirm button is clicked", () => {
    const onConfirm = vi.fn();
    render(<WhatChangedReview mode="extraction" changes={CHANGES} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByTestId("what-changed-confirm"));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("renders a skip button only when onDismiss is provided", () => {
    const { rerender } = render(<WhatChangedReview mode="extraction" changes={CHANGES} />);
    expect(screen.queryByTestId("what-changed-skip")).toBeNull();
    rerender(<WhatChangedReview mode="extraction" changes={CHANGES} onDismiss={vi.fn()} />);
    expect(screen.getByTestId("what-changed-skip")).toBeInTheDocument();
  });

  it("calls onFix with the specific change (Branch G)", () => {
    const onFix = vi.fn();
    render(<WhatChangedReview mode="merge" changes={CHANGES} onFix={onFix} />);
    const fixButtons = screen.getAllByTestId("what-changed-fix");
    fireEvent.click(fixButtons[0]);
    expect(onFix).toHaveBeenCalledWith(CHANGES[0]);
  });

  it("uses the attestation confirm label in download mode", () => {
    render(<WhatChangedReview mode="download" changes={CHANGES} onConfirm={vi.fn()} />);
    expect(screen.getByTestId("what-changed-confirm")).toHaveTextContent("confirmDownload");
  });

  it("shows an empty state when there are no changes", () => {
    render(<WhatChangedReview mode="merge" changes={[]} />);
    expect(screen.getByTestId("what-changed-empty")).toBeInTheDocument();
  });
});
