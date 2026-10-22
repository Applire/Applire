// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

vi.mock("next-intl", () => ({
  useTranslations: () =>
    Object.assign(
      (key: string, values?: Record<string, unknown>) =>
        values ? `${key}(${Object.values(values).join("|")})` : key,
      { has: () => true },
    ),
}));

const getReceiptSummary = vi.fn();
vi.mock("@/lib/api/review", () => ({
  getReceiptSummary: (sid?: string | null) => getReceiptSummary(sid),
}));

import { ReceiptSummaryReview } from "../ReceiptSummaryReview";

const LONG =
  "Verantwortung für die Auswahl, Pilotlinie und den Rollout eines MES-Systems zur Maschinen- und Betriebsdatenerfassung an 14 Spritzgussmaschinen in beide Fertigungsbereiche.";

const SUMMARY = {
  session_id: "s1",
  answers: 3,
  facts_total: 5,
  groups: [
    { section: "work_experience", count: 3, entities: [{ label: "Produktionsleiter @ Weberit", count: 3 }], preview: [], more: 0 },
    { section: "skills", count: 1, entities: [], preview: ["SAP PP"], more: 0 },
    { section: "limits", count: 1, entities: [], preview: ["IFS"], more: 0 },
  ],
  top: [
    { key: "r", section: "work_experience", kind: "result", action: "merged", text: "Termintreue von 87 % auf 96 % verbessert.", entity_label: "Produktionsleiter @ Weberit", also_in: [] },
  ],
  facts: [
    { key: "a", section: "work_experience", kind: "responsibility", action: "merged", text: LONG, entity_label: "Produktionsleiter @ Weberit", also_in: [] },
    { key: "r", section: "work_experience", kind: "result", action: "merged", text: "Termintreue von 87 % auf 96 % verbessert.", entity_label: "Produktionsleiter @ Weberit", also_in: [] },
    { key: "t", section: "work_experience", kind: "tool", action: "merged", text: "SMED", entity_label: "Produktionsleiter @ Weberit", also_in: [] },
    { key: "s", section: "skills", kind: "skill", action: "added", text: "SAP PP", entity_label: "Produktionsleiter @ Weberit", also_in: ["work_experience"] },
    { key: "l", section: "limits", kind: "limit", action: "added", text: "IFS", entity_label: null, also_in: [] },
  ],
};

describe("ReceiptSummaryReview (ruling V-1 = A)", () => {
  beforeEach(() => {
    getReceiptSummary.mockReset();
    getReceiptSummary.mockResolvedValue(SUMMARY);
  });

  it("asks for THIS session's summary", async () => {
    render(<ReceiptSummaryReview sessionId="s1" />);
    await waitFor(() => expect(screen.getByTestId("receipt-summary")).toBeInTheDocument());
    expect(getReceiptSummary).toHaveBeenCalledWith("s1");
  });

  it("shows a lead, one card per section and the key points — the full list stays folded", async () => {
    render(<ReceiptSummaryReview sessionId="s1" />);
    await waitFor(() => expect(screen.getAllByTestId("receipt-summary-group")).toHaveLength(3));
    expect(screen.getByTestId("receipt-summary-lead")).toHaveTextContent("summary.lead(5)");
    const work = screen.getAllByTestId("receipt-summary-group")[0];
    expect(work).toHaveTextContent("summary.points(2)");
    expect(work).toHaveTextContent("summary.tools(1)");
    expect(work).toHaveTextContent("Produktionsleiter – Weberit");
    expect(within(screen.getByTestId("receipt-summary-top")).getByText(/Termintreue/)).toBeInTheDocument();
    expect(screen.queryByTestId("receipt-summary-full")).toBeNull();
    expect(screen.queryByText(LONG)).toBeNull();
  });

  it("the expander shows every fact once, untruncated, with where else it was written", async () => {
    const onFix = vi.fn();
    render(<ReceiptSummaryReview sessionId="s1" onFix={onFix} />);
    const expand = await screen.findByTestId("receipt-summary-expand");
    expect(expand).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(expand);
    expect(expand).toHaveAttribute("aria-expanded", "true");
    const rows = screen.getAllByTestId("receipt-summary-row");
    expect(rows).toHaveLength(5);
    const long = screen.getByText(LONG);
    expect(long.className).not.toMatch(/truncate/);
    expect(screen.getByText("summary.alsoAt(Produktionsleiter – Weberit)")).toBeInTheDocument();
    fireEvent.click(within(rows[3]).getByTestId("receipt-summary-fix"));
    expect(onFix).toHaveBeenCalledWith(expect.objectContaining({ text: "SAP PP" }));
  });

  it("an empty session says so", async () => {
    getReceiptSummary.mockResolvedValue({ ...SUMMARY, facts_total: 0, groups: [], top: [], facts: [] });
    render(<ReceiptSummaryReview />);
    expect(await screen.findByTestId("receipt-summary-empty")).toBeInTheDocument();
  });
});
