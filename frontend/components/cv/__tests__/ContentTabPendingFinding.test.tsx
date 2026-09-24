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
 * ADR-090 cl. 5 — *Let me edit it* on a review finding: open the section
 * that holds the `placeIndex`-th place of `targets`, nothing pre-filled. Two
 * halves: the pure function `sectionHoldingPlace` (counted over sections in
 * document order, same normalisation as *Show me where*), and the render
 * wiring that fires it once the sections have loaded.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach, beforeEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ContentTab, sectionHoldingPlace, type SectionItem } from "../ContentTab";
import type { LocateTarget } from "@/lib/locate-in-preview";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

function section(id: string, content: string, overrides: Partial<SectionItem> = {}): SectionItem {
  return { section_id: id, label: id, content, has_override: false, gaps: [], ...overrides };
}

describe("sectionHoldingPlace (ADR-090 cl. 5)", () => {
  it("place 0 in the first section returns the first section", () => {
    const sections = [section("intro", "Led the AI governance rollout"), section("skills", "Python, React")];
    const targets: LocateTarget[] = [{ form: "AI governance" }];
    expect(sectionHoldingPlace(sections, targets, 0)).toBe("intro");
  });

  it("a place index spilling past the first section's hits returns the second section", () => {
    // "intro" holds ONE place (place 0); "skills" holds the next ones. Asking
    // for place index 2 must spill past intro's single hit into skills — a
    // per-section (rather than running/global) count would return "intro"
    // for every index, which this pins against.
    const sections = [
      section("intro", "AI governance lead"),
      section("skills", "AI governance, AI governance, AI governance"),
    ];
    const targets: LocateTarget[] = [{ form: "AI governance" }];
    // Global order: intro[0], skills[0]=index1, skills[1]=index2, skills[2]=index3
    expect(sectionHoldingPlace(sections, targets, 2)).toBe("skills");
  });

  it("a placeIndex past the end falls back to the first section that holds any place", () => {
    const sections = [section("intro", "no match here"), section("skills", "AI governance")];
    const targets: LocateTarget[] = [{ form: "AI governance" }];
    // Only one place exists (in "skills"); asking for index 99 must fall back
    // to it rather than returning null or the first section unconditionally.
    expect(sectionHoldingPlace(sections, targets, 99)).toBe("skills");
  });

  it("falls back to the first section with a hit even when place 0 IS that hit", () => {
    const sections = [section("intro", "no match"), section("skills", "AI governance"), section("extra", "AI governance")];
    const targets: LocateTarget[] = [{ form: "AI governance" }];
    expect(sectionHoldingPlace(sections, targets, 0)).toBe("skills");
  });

  it("null targets return null — the section list stays open", () => {
    const sections = [section("intro", "AI governance")];
    expect(sectionHoldingPlace(sections, null, 0)).toBeNull();
  });

  it("an empty targets array returns null", () => {
    const sections = [section("intro", "AI governance")];
    expect(sectionHoldingPlace(sections, [], 0)).toBeNull();
  });

  it("no section holding any place returns null (no fallback exists)", () => {
    const sections = [section("intro", "nothing"), section("skills", "nothing here either")];
    const targets: LocateTarget[] = [{ form: "AI governance" }];
    expect(sectionHoldingPlace(sections, targets, 0)).toBeNull();
  });
});

describe("ContentTab — pendingFinding (ADR-090 cl. 5)", () => {
  const SECTIONS = [
    { section_id: "intro", label: "Introduction", content: "no match here", has_override: false, gaps: [] },
    { section_id: "skills", label: "Skills", content: "Delivered AI governance training", has_override: false, gaps: [] },
  ];

  const BASE = {
    cvId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    flowSummary: {
      job_summary: "Operations Manager",
      gap_summary: { gaps: [], sections: SECTIONS },
      cv_summary: { sections: SECTIONS },
    },
    onSectionSave: vi.fn(),
    onUnsavedChange: vi.fn(),
    variant: "sections" as const,
  };

  beforeEach(() => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ sections: SECTIONS, general_gaps: [] }),
    } as Response);
  });
  afterEach(() => vi.restoreAllMocks());

  it("opens the section holding the place once the sections load", async () => {
    const onConsumed = vi.fn();
    render(
      withIntl(
        <ContentTab
          {...BASE}
          pendingFinding={{ targets: [{ form: "AI governance" }], placeIndex: 0, nonce: 1 }}
          onPendingFindingConsumed={onConsumed}
        />,
      ),
    );
    // The section editor replaces the browse list — its own content is the
    // signal we are in the "skills" section specifically, not merely in SOME
    // editor (which a wrong-section bug could still satisfy).
    await waitFor(() => expect(screen.getByTestId("section-textarea")).toBeTruthy());
    expect((screen.getByTestId("section-textarea") as HTMLTextAreaElement).value).toBe(
      "Delivered AI governance training",
    );
    expect(screen.getAllByText("Skills").length).toBeGreaterThan(0);
    expect(onConsumed).toHaveBeenCalledTimes(1);
  });

  it("does nothing without a pending finding", async () => {
    render(withIntl(<ContentTab {...BASE} />));
    await waitFor(() => expect(screen.getByText("Introduction")).toBeTruthy());
    expect(screen.queryByTestId("section-textarea")).toBeNull();
  });

  it("consumes a nonce exactly once — a re-render cannot re-open the editor", async () => {
    const onConsumed = vi.fn();
    const props = {
      ...BASE,
      pendingFinding: { targets: [{ form: "AI governance" }], placeIndex: 0, nonce: 7 },
      onPendingFindingConsumed: onConsumed,
    };
    const { rerender } = render(withIntl(<ContentTab {...props} />));
    await waitFor(() => expect(onConsumed).toHaveBeenCalledTimes(1));
    rerender(withIntl(<ContentTab {...props} />));
    await waitFor(() => expect(onConsumed).toHaveBeenCalledTimes(1));
  });
});
