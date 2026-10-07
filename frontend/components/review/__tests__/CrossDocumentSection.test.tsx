// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * #702 (RULING R-2 = A) — the cross-document section: one card per letter
 * sentence, three actions, decisions label items, unknown when the critic did
 * not run. The item is the backend's derived shape for the synthetic
 * operations_marcus_de run of 2026-09-13.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { CrossDocumentSection } from "../CrossDocumentSection";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import type { ReviewState } from "@/lib/api/document-review";

const api = vi.hoisted(() => ({
  takeOut: vi.fn(),
  keepCrossDocument: vi.fn(),
  undoDecision: vi.fn(),
}));
vi.mock("@/lib/api/document-review", async (orig) => {
  const real = await orig<typeof import("@/lib/api/document-review")>();
  return { ...real, takeOut: api.takeOut, keepCrossDocument: api.keepCrossDocument, undoDecision: api.undoDecision };
});

const TRANSFER =
  "Hygiene- und Dokumentationsdisziplin aus Kosmetik-Verpackungen, einem Sauberraumbereich seit 2021 und zehn Jahren ISO-9001-Audit-Praxis sowie neun Jahre Kunststofftechnik mit Spritzguss und Montage sind jedoch übertragbare Grundlagen.";
const KEY_HIGH = "critic:hygiene";
const KEY_NORMAL = "critic:meine englischpraxis";

const REPORT: OutcomeCriticReport = {
  ran: true,
  mount: "letter",
  dropped_citations: 0,
  advisories: [],
  cross_document: [
    {
      key: KEY_HIGH,
      letter_state: TRANSFER,
      concepts: ["Kosmetik-Verpackungen", "Sauberraumbereich seit 2021", "ISO-9001-Audit-Praxis"],
      kinds: ["letter_only"],
      weight: "high",
      figures: ["zehn Jahren", "2021"],
    },
    {
      key: KEY_NORMAL,
      letter_state: "Meine Englischpraxis umfasst Werksbesuche und Quartalspräsentationen.",
      concepts: ["Quartalspräsentationen"],
      kinds: ["letter_only"],
      weight: "normal",
      figures: [],
    },
  ],
};

function renderSection(over: Partial<Parameters<typeof CrossDocumentSection>[0]> = {}, locale: "en" | "de" = "en") {
  const onRefresh = vi.fn();
  render(
    withIntl(
      <CrossDocumentSection
        documentKind="cover-letter"
        documentId="cl-1"
        criticReport={REPORT}
        reviewState={null}
        cvEditHref="/flow/f1/cv?tab=edit"
        onRefresh={onRefresh}
        {...over}
      />,
      locale,
    ),
  );
  return { onRefresh };
}

beforeEach(() => {
  api.takeOut.mockReset();
  api.keepCrossDocument.mockReset();
  api.undoDecision.mockReset();
});

describe("CrossDocumentSection", () => {
  it("opens the high-weight item as a card with the kicker, concepts and marked quote", () => {
    renderSection();
    const card = screen.getByTestId("review-xdoc-card");
    expect(card.dataset.weight).toBe("high");
    expect(screen.getByTestId("review-xdoc-kicker")).toHaveTextContent("Stands out when cross-read");
    expect(card).toHaveTextContent("In the cover letter, not in the CV");
    expect(card).toHaveTextContent("Your cover letter states 3 things here that your CV never mentions.");
    expect(card).toHaveTextContent("especially with years and durations attached");
    expect(screen.getByTestId("review-xdoc-quote").querySelectorAll("mark")).toHaveLength(3);
    expect(screen.getByTestId("review-xdoc-sub")).toHaveTextContent("0 of 2 decided");
  });

  it("counts and marks only the letter-only facts when a letter-richer clause shares the sentence", () => {
    const item = {
      ...REPORT!.cross_document![0],
      concepts: [...REPORT!.cross_document![0].concepts, "Kunststofftechnik"],
      letter_only: ["Kosmetik-Verpackungen", "Sauberraumbereich seit 2021", "ISO-9001-Audit-Praxis"],
      kinds: ["letter_only" as const, "letter_richer" as const],
    };
    renderSection({ criticReport: { ...REPORT!, cross_document: [item] } });
    expect(screen.getByTestId("review-xdoc-card")).toHaveTextContent("states 3 things");
    expect(screen.getByTestId("review-xdoc-concepts")).not.toHaveTextContent("Kunststofftechnik");
    expect(screen.getByTestId("review-xdoc-quote").querySelectorAll("mark")).toHaveLength(3);
  });

  it("speaks German with the real translations", () => {
    renderSection({}, "de");
    expect(screen.getByText("Gegengelesen: Anschreiben und Lebenslauf")).toBeInTheDocument();
    expect(screen.getByTestId("review-xdoc-card")).toHaveTextContent("Im Anschreiben, nicht im Lebenslauf");
    expect(screen.getByTestId("review-xdoc-add-to-cv")).toHaveTextContent("In den Lebenslauf übernehmen");
    expect(screen.getByTestId("review-xdoc-take-out")).toHaveTextContent("Aus dem Anschreiben nehmen");
    expect(screen.getByTestId("review-xdoc-keep")).toHaveTextContent("So lassen");
  });

  it("the normal item is a row that opens as the card when clicked", () => {
    renderSection();
    const row = screen.getByTestId("review-xdoc-row");
    expect(row).toHaveTextContent("Quartalspräsentationen");
    fireEvent.click(row);
    expect(screen.getByTestId("review-xdoc-card").dataset.weight).toBe("normal");
    expect(screen.queryByTestId("review-xdoc-kicker")).toBeNull();
  });

  it("add-to-CV links to the CV's Edit tab and is hidden without a target", () => {
    renderSection();
    expect(screen.getByTestId("review-xdoc-add-to-cv").getAttribute("href")).toBe("/flow/f1/cv?tab=edit");
  });

  it("hides add-to-CV when no CV edit target exists", () => {
    renderSection({ cvEditHref: null });
    expect(screen.queryByTestId("review-xdoc-add-to-cv")).toBeNull();
  });

  it("take-out calls the removal endpoint with the critic key and hands the refresh up", async () => {
    api.takeOut.mockResolvedValue({ changes: [{ section_id: "body", before: "a", after: "b" }], still_listed: false, review_state: {} });
    const { onRefresh } = renderSection();
    fireEvent.click(screen.getByTestId("review-xdoc-take-out"));
    await waitFor(() => expect(api.takeOut).toHaveBeenCalledWith("cover-letter", "cl-1", KEY_HIGH));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledWith(expect.anything(), { documentChanged: true }));
  });

  it("says so when the wording is still in the letter after take-out", async () => {
    api.takeOut.mockResolvedValue({ changes: [], still_listed: true, review_state: {} });
    renderSection();
    fireEvent.click(screen.getByTestId("review-xdoc-take-out"));
    await waitFor(() => expect(screen.getByTestId("review-xdoc-still-there")).toBeInTheDocument());
  });

  it("keep records the decision", async () => {
    api.keepCrossDocument.mockResolvedValue({ review_state: { walked_at: null, decisions: [] } });
    const { onRefresh } = renderSection();
    fireEvent.click(screen.getByTestId("review-xdoc-keep"));
    await waitFor(() => expect(api.keepCrossDocument).toHaveBeenCalledWith("cover-letter", "cl-1", KEY_HIGH, true));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledWith({ review_state: { walked_at: null, decisions: [] } }, { documentChanged: false }));
  });

  it("a kept item shows its status and undo withdraws the decision", async () => {
    const state: ReviewState = {
      walked_at: null,
      decisions: [{ finding_key: KEY_HIGH, label: TRANSFER, action: "kept", at: "2026-10-07T12:00:00Z", undo: null }],
    };
    api.keepCrossDocument.mockResolvedValue({ review_state: { walked_at: null, decisions: [] } });
    renderSection({ reviewState: state });
    // the open normal item is now the card; the kept one is a decided row
    const decided = screen.getAllByTestId("review-xdoc-row").find((r) => r.dataset.status === "kept")!;
    expect(decided).toHaveTextContent("Kept");
    expect(screen.getByTestId("review-xdoc-sub")).toHaveTextContent("1 of 2 decided");
    fireEvent.click(decided);
    fireEvent.click(screen.getByTestId("review-xdoc-undo"));
    await waitFor(() => expect(api.keepCrossDocument).toHaveBeenCalledWith("cover-letter", "cl-1", KEY_HIGH, false));
  });

  it("a taken-out item's undo goes through the shared undo endpoint", async () => {
    const state: ReviewState = {
      walked_at: null,
      decisions: [{ finding_key: KEY_NORMAL, label: "x", action: "taken_out", at: "2026-10-07T12:00:00Z", undo: null }],
    };
    api.undoDecision.mockResolvedValue({ review_state: {} });
    renderSection({ reviewState: state });
    fireEvent.click(screen.getAllByTestId("review-xdoc-row").find((r) => r.dataset.status === "taken_out")!);
    fireEvent.click(screen.getByTestId("review-xdoc-undo"));
    await waitFor(() => expect(api.undoDecision).toHaveBeenCalledWith("cover-letter", "cl-1", KEY_NORMAL));
  });

  it("states unknown — never an empty section — when the critic did not run", () => {
    renderSection({ criticReport: { ...REPORT!, ran: false } });
    expect(screen.getByTestId("review-xdoc-unknown")).toHaveTextContent("Not checked");
    expect(screen.queryByTestId("review-xdoc")).toBeNull();
  });

  it("renders nothing on the CV and nothing when there is no item", () => {
    renderSection({ documentKind: "cv", criticReport: null });
    expect(screen.queryByTestId("review-xdoc-unknown")).toBeNull();
    renderSection({ criticReport: { ...REPORT!, cross_document: [] } });
    expect(screen.queryByTestId("review-xdoc")).toBeNull();
  });
});
