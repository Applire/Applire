// Copyright (C) 2026 Tobias Rosenbaum
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
 * #763 (ruling ADV-1) — "Geschichte erzählen" on /gaps: a story answer that
 * wrote nothing shows the approved receipt copy (COPY.md), never the
 * "Geschichte hinzugefügt" success line; a turn that wrote keeps it.
 */
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { LiabilityPanel } from "../LiabilityPanel";
import type { GapCluster } from "@/lib/match-utils";
import { withIntl } from "@/lib/test-utils/with-intl";

const CLUSTERS: GapCluster[] = [
  {
    id: "cluster-rag",
    label: "RAG experience",
    category: "C" as const,
    gaps: ["RAG"],
    jd_skills: ["RAG"],
    jd_context: "",
    outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
    coverage: "open",
    budget_remaining: 2,
    member_statuses: [{ member: "RAG", status: "gap" }],
  },
];

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => body } as Response;
}

async function tellAndAnswer(turn: Record<string, unknown>, locale: "de" | "en", onStoryAdded = vi.fn()) {
  (global.fetch as ReturnType<typeof vi.fn>)
    .mockResolvedValueOnce(jsonResponse({ session_id: "s1", question: "Tell me about RAG.", choices: null }))
    .mockResolvedValueOnce(jsonResponse(turn));
  render(
    withIntl(
      <LiabilityPanel
        jobId="job-1"
        liabilities={[{ concept: "RAG" }]}
        clusters={CLUSTERS}
        apiBase=""
        onDropped={() => {}}
        onStoryAdded={onStoryAdded}
      />,
      locale,
    ),
  );
  fireEvent.click(screen.getByTestId("liability-tell-story-RAG"));
  await waitFor(() => screen.getByTestId("liability-answer-textarea-RAG"));
  fireEvent.change(screen.getByTestId("liability-answer-textarea-RAG"), {
    target: { value: "Last year I was on holiday in Portugal and read a lot." },
  });
  fireEvent.click(screen.getByTestId("liability-submit-RAG"));
  return onStoryAdded;
}

describe("LiabilityPanel — #763 receipt instead of a false success line", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it.each([
    ["de", "no_write", "Von dem, was du gesagt hast, wurde nichts übernommen — sag es noch einmal in einem Satz, oder trag es im Profil ein."],
    ["de", "no_write_already_known", "Das steht so schon in deinem Profil."],
    ["en", "no_write_question_only", "Your question arrived — nothing was changed for it."],
  ] as const)("[%s] a completed turn that wrote nothing (%s) shows the receipt", async (locale, reason, sentence) => {
    const onStoryAdded = await tellAndAnswer({ complete: true, no_write_receipt: reason }, locale);
    await waitFor(() => screen.getByTestId("liability-receipt-RAG"));
    expect(screen.getByTestId("liability-receipt-RAG")).toHaveTextContent(sentence);
    expect(screen.queryByTestId("liability-resolved-RAG")).toBeNull();
    expect(onStoryAdded).not.toHaveBeenCalled();
    // The story can be told again.
    expect(screen.getByTestId("liability-tell-story-RAG")).toBeInTheDocument();
  });

  it("a completed turn that wrote something keeps the success line", async () => {
    const onStoryAdded = await tellAndAnswer({ complete: true, changes_applied: true, no_write_receipt: null }, "de");
    await waitFor(() => screen.getByTestId("liability-resolved-RAG"));
    expect(screen.getByTestId("liability-resolved-RAG")).toHaveTextContent("Geschichte hinzugefügt");
    expect(screen.queryByTestId("liability-receipt-RAG")).toBeNull();
    expect(onStoryAdded).toHaveBeenCalledWith("RAG");
  });

  it("a follow-up turn that wrote nothing shows the receipt above the next question", async () => {
    await tellAndAnswer(
      { complete: false, question: "Which retriever did you use?", no_write_receipt: "no_write" },
      "en",
    );
    await waitFor(() => screen.getByTestId("liability-receipt-RAG"));
    expect(screen.getByTestId("liability-receipt-RAG")).toHaveTextContent("Nothing was recorded");
    expect(screen.getByTestId("liability-question-RAG")).toHaveTextContent("Which retriever");
  });

  it("the receipt outlives the refresh that drops the concept from the list, until closed", async () => {
    // Re-run R2 ("Software testing"): the completing turn re-reads the
    // analysis and the concept is no longer a liability.
    (global.fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(jsonResponse({ session_id: "s1", question: "Tell me about RAG.", choices: null }))
      .mockResolvedValueOnce(jsonResponse({ complete: true, no_write_receipt: "no_write" }));
    const panel = (liabilities: { concept: string }[]) =>
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={liabilities}
          clusters={CLUSTERS}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={() => {}}
        />,
        "de",
      );
    const { rerender } = render(panel([{ concept: "RAG" }]));
    fireEvent.click(screen.getByTestId("liability-tell-story-RAG"));
    await waitFor(() => screen.getByTestId("liability-answer-textarea-RAG"));
    fireEvent.change(screen.getByTestId("liability-answer-textarea-RAG"), {
      target: { value: "Last year I was on holiday in Portugal and read a lot." },
    });
    fireEvent.click(screen.getByTestId("liability-submit-RAG"));
    await waitFor(() => screen.getByTestId("liability-receipt-RAG"));

    rerender(panel([]));

    expect(screen.getByTestId("liability-receipt-RAG")).toHaveTextContent("wurde nichts übernommen");
    expect(screen.queryByTestId("liability-tell-story-RAG")).toBeNull();
    // A kept receipt card is not a keyword that still needs a story.
    expect(screen.queryByTestId("liability-badge")).toBeNull();
    fireEvent.click(screen.getByTestId("liability-receipt-close-RAG"));
    expect(screen.queryByTestId("liability-card-RAG")).toBeNull();
  });
});
