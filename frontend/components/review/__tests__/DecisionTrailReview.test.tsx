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

vi.mock("next-intl", () => ({ useTranslations: () => Object.assign((key: string) => key, { has: () => true }) }));

const getProfileChanges = vi.fn();
vi.mock("@/lib/api/review", () => ({ getProfileChanges: () => getProfileChanges() }));

import { DecisionTrailReview } from "../DecisionTrailReview";

const TRAIL = {
  enrichmentHistory: [
    { source: "cv_upload", timestamp: "t1", changes: [{ section: "work_experience", field: "work_experience", action: "merged", newValue: "MERGE_ROW", rationale: "merged at upload" }] },
    { source: "interview", timestamp: "t2", changes: [{ section: "skills", field: "skills", action: "added", newValue: "INTERVIEW_ROW", rationale: "from your answer" }] },
  ],
  pendingConflicts: [{ section: "work_experience", field: "start_date", action: "updated", oldValue: "2020-01", newValue: "2019-01", rationale: "differs" }],
};

describe("DecisionTrailReview", () => {
  beforeEach(() => {
    getProfileChanges.mockReset();
    getProfileChanges.mockResolvedValue(TRAIL);
  });

  it("merge mode shows non-interview changes and pending conflicts", async () => {
    render(<DecisionTrailReview mode="merge" />);
    await waitFor(() => expect(screen.getByTestId("what-changed-review")).toHaveAttribute("data-mode", "merge"));
    expect(screen.getByText("merged at upload")).toBeInTheDocument();
    expect(screen.getByText("differs")).toBeInTheDocument();          // pending conflict surfaced
    expect(screen.queryByText("from your answer")).toBeNull();         // interview row excluded
  });

  it("interview mode shows only interview changes", async () => {
    render(<DecisionTrailReview mode="interview" />);
    await waitFor(() => expect(screen.getByTestId("what-changed-review")).toHaveAttribute("data-mode", "interview"));
    expect(screen.getByText("from your answer")).toBeInTheDocument();
    expect(screen.queryByText("merged at upload")).toBeNull();
  });

  it("extraction mode shows only what was read in (added) from the CV", async () => {
    getProfileChanges.mockResolvedValue({
      enrichmentHistory: [
        { source: "cv_upload", timestamp: "t", changes: [
          { section: "work_experience", field: "work_experience", action: "added", newValue: "READ_ROW", rationale: "read from CV" },
          { section: "work_experience", field: "work_experience", action: "merged", newValue: "ASSUMED_ROW", rationale: "assumed" },
        ] },
      ],
      pendingConflicts: [],
    });
    render(<DecisionTrailReview mode="extraction" onConfirm={vi.fn()} onDismiss={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("what-changed-review")).toHaveAttribute("data-mode", "extraction"));
    expect(screen.getByText("read from CV")).toBeInTheDocument();
    expect(screen.queryByText("assumed")).toBeNull();        // merged-assumption excluded from extraction confirm
    expect(screen.getByTestId("what-changed-skip")).toBeInTheDocument();  // skippable (ADR-040 §4)
  });

  it("forwards onFix (Branch G)", async () => {
    const onFix = vi.fn();
    render(<DecisionTrailReview mode="interview" onFix={onFix} />);
    await waitFor(() => screen.getByTestId("what-changed-fix"));
    fireEvent.click(screen.getByTestId("what-changed-fix"));
    expect(onFix).toHaveBeenCalledOnce();
  });
});
