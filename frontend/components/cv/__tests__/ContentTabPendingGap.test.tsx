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
 * #667 — the receiving half of the fourth handle (ADR-081 cl. 3, amended
 * 2026-09-11): the review surface hands ContentTab a gap, and ContentTab opens
 * the section editor with it preselected.
 *
 * The routing DECISION stays ContentTab's (`handleAddressGap`): an honest gap
 * goes to profile enrichment (#117 / ADR-019 — never invite a written claim)
 * and only a claimable one opens the editor. These tests pin that the handle
 * did not quietly move that decision to the caller.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach, beforeEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ContentTab } from "../ContentTab";

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush }) }));

const SECTIONS = [
  {
    section_id: "introduction",
    label: "Introduction",
    content: "Experienced dev",
    has_override: false,
    gaps: [
      { id: "sap-pp", label: "SAP PP", kind: "claimable" as const },
      { id: "terraform", label: "Terraform", kind: "honest" as const },
    ],
  },
  { section_id: "skills", label: "Skills", content: "Python", has_override: false, gaps: [] },
];

const BASE = {
  cvId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  flowSummary: {
    job_summary: "Operations Manager",
    gap_summary: { gaps: SECTIONS[0].gaps, sections: SECTIONS },
    cv_summary: { sections: SECTIONS },
  },
  onSectionSave: vi.fn(),
  onUnsavedChange: vi.fn(),
  variant: "sections" as const,
};

describe("ContentTab — pendingGap (#667)", () => {
  beforeEach(() => {
    // The router mock is module-level, so `restoreAllMocks` does not reset it;
    // a leaked call from the previous test would read as this one's.
    mockPush.mockClear();
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ sections: SECTIONS, general_gaps: [] }),
    } as Response);
  });
  afterEach(() => vi.restoreAllMocks());

  it("opens the owning section's editor for a CLAIMABLE gap", async () => {
    const onConsumed = vi.fn();
    render(
      withIntl(
        <ContentTab
          {...BASE}
          pendingGap={{ gapId: "sap-pp", nonce: 1 }}
          onPendingGapConsumed={onConsumed}
        />,
      ),
    );
    // The editor replaces the browse list — the section's own content is the
    // signal that we are in it, not merely that the list rendered.
    await waitFor(() => expect(screen.getByTestId("section-textarea")).toBeTruthy());
    expect(onConsumed).toHaveBeenCalledTimes(1);
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("routes an HONEST gap to profile enrichment instead (#117)", async () => {
    render(withIntl(<ContentTab {...BASE} pendingGap={{ gapId: "terraform", nonce: 1 }} />));
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/profile"));
    expect(screen.queryByTestId("section-textarea")).toBeNull();
  });

  it("does nothing at all without a pending gap", async () => {
    render(withIntl(<ContentTab {...BASE} />));
    await waitFor(() => expect(screen.getByText("Introduction")).toBeTruthy());
    expect(screen.queryByTestId("section-textarea")).toBeNull();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("consumes a nonce exactly once — a re-render cannot re-open the editor", async () => {
    const onConsumed = vi.fn();
    const props = {
      ...BASE,
      pendingGap: { gapId: "sap-pp", nonce: 4 },
      onPendingGapConsumed: onConsumed,
    };
    const { rerender } = render(withIntl(<ContentTab {...props} />));
    await waitFor(() => expect(onConsumed).toHaveBeenCalledTimes(1));
    rerender(withIntl(<ContentTab {...props} />));
    await waitFor(() => expect(onConsumed).toHaveBeenCalledTimes(1));
  });
});
