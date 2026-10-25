// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * R2-8 (9) (Hazelnut build 4, ruling V-2 = A) — "See what was combined" lands
 * on the last import's summary; the technical log is folded inside it.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ImportSummaryCard } from "../ImportSummaryCard";
import { withIntl } from "@/lib/test-utils/with-intl";

const SUMMARY = {
  record_id: "r1",
  timestamp: "2026-10-23T09:00:00Z",
  source: "cv_upload",
  file_name: "Lebenslauf_Stefan_Brandt.pdf",
  added_total: 21,
  groups: [
    { section: "work_experience", count: 14, entities: [{ label: "Produktionsleiter @ Verpackungswerk Nord", count: 9 }], preview: [], more: 0 },
    { section: "skills", count: 5, entities: [], preview: ["SAP PP", "SMED"], more: 3 },
  ],
  matched: [
    { section: "skills", entity_id: "s1", incoming: "Budgetverantwortung", existing: "Budgetplanung", basis: "model" },
  ],
  kept_separate: [{ section: "skills", entity_id: "s-ment", incoming: "REST APIs", existing: "Mentoring" }],
  restated: [],
  not_applied_total: 0,
};

function respond(body: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200 })));
}

afterEach(() => vi.restoreAllMocks());

describe("ImportSummaryCard", () => {
  it("summarises the last import in the user's words, the log folded", async () => {
    respond(SUMMARY);
    render(withIntl(<ImportSummaryCard><div data-testid="the-log">log</div></ImportSummaryCard>, "de"));
    await waitFor(() => expect(screen.getByTestId("import-summary")).toBeInTheDocument());
    expect(screen.getByTestId("import-summary")).toHaveAttribute("id", "import-summary");
    expect(screen.getByText("Dein Import vom 23.10.2026 — Lebenslauf_Stefan_Brandt.pdf")).toBeInTheDocument();
    expect(screen.getByTestId("import-summary-lead")).toHaveTextContent(
      "Wir haben 21 Angaben in dein Profil übernommen und 1 als schon vorhanden erkannt. Nichts wurde gelöscht.",
    );
    expect(screen.getByTestId("import-summary-groups")).toHaveTextContent("Produktionsleiter @ Verpackungswerk Nord: 9");
    expect(screen.getByTestId("import-summary-groups")).toHaveTextContent("SAP PP, SMED, +3");
    expect(screen.getByTestId("recognised-match-question")).toHaveTextContent("Ist Budgetverantwortung dasselbe wie Budgetplanung?");
    expect(screen.getByTestId("kept-separately")).toBeInTheDocument();
    const log = screen.getByTestId("import-tech-log");
    expect(log.tagName).toBe("DETAILS");
    expect(log).not.toHaveAttribute("open");
    expect(log).toHaveTextContent("Technisches Protokoll anzeigen");
    expect(screen.getByTestId("the-log")).toBeInTheDocument();
  });

  it("without an import in the trail the log renders as before", async () => {
    respond({ ...SUMMARY, record_id: null });
    render(withIntl(<ImportSummaryCard><div data-testid="the-log">log</div></ImportSummaryCard>, "de"));
    await waitFor(() => expect(screen.getByTestId("the-log")).toBeInTheDocument());
    expect(screen.queryByTestId("import-summary")).toBeNull();
    expect(screen.queryByTestId("import-tech-log")).toBeNull();
  });
});
