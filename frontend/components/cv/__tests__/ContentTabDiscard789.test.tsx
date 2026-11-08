// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/** 789:265/266 — edit affordance on rows; discard returns to the list with a receipt. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ContentTab } from "../ContentTab";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const SECTIONS = [
  { section_id: "introduction", label: "Introduction", content: "Ops lead", has_override: true, gaps: [] },
  { section_id: "position::p1", label: "Lead — Acme", content: "A\nB", has_override: false, gaps: [] },
];

function renderTab(locale: "de" | "en" = "de") {
  vi.spyOn(global, "fetch").mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ sections: SECTIONS, general_gaps: [] }),
  } as Response);
  const onUnsavedChange = vi.fn();
  render(
    withIntl(
      <ContentTab
        cvId="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        flowSummary={null}
        onSectionSave={vi.fn()}
        onUnsavedChange={onUnsavedChange}
        variant="sections"
      />,
      locale,
    ),
  );
  return { onUnsavedChange };
}

describe("ContentTab — edit affordance and discard receipt", () => {
  afterEach(() => vi.restoreAllMocks());

  it("every row shows the Bearbeiten affordance (de) / Edit (en)", async () => {
    renderTab("de");
    const row = await screen.findByTestId("edit-section-row-position::p1");
    expect(row.textContent).toContain("Bearbeiten");
    expect(screen.getByTestId("edit-section-row-introduction").textContent).toContain("Bearbeiten");
  });

  it("en label", async () => {
    renderTab("en");
    const row = await screen.findByTestId("edit-section-row-position::p1");
    expect(row.textContent).toContain("Edit");
  });

  it("discard returns to the list, names the section, and clears when a section opens", async () => {
    const { onUnsavedChange } = renderTab("de");
    fireEvent.click(await screen.findByTestId("edit-section-row-position::p1"));
    fireEvent.change(await screen.findByTestId("section-bullet-0"), { target: { value: "changed" } });
    fireEvent.click(screen.getByTestId("section-cancel"));
    await waitFor(() => expect(screen.queryByTestId("section-bullet-0")).toBeNull());
    expect(onUnsavedChange).toHaveBeenLastCalledWith(false, null);
    expect(screen.getByTestId("section-discarded-receipt").textContent).toBe(
      "Änderung an „Lead — Acme“ verworfen.",
    );
    fireEvent.click(screen.getByTestId("edit-section-row-introduction"));
    await screen.findByTestId("section-textarea");
    expect(screen.queryByTestId("section-discarded-receipt")).toBeNull();
  });
});
