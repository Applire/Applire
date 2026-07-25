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

/**
 * LiabilityPanel — #260 pre-generation keyword-liability summary. Two honest
 * exits: tell the story (resolve_gap micro-session, reusing the SAME
 * gap_cluster machinery) or drop the keyword (deterministic downgrade).
 */
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { LiabilityPanel } from "../LiabilityPanel";
import { withIntl } from "@/lib/test-utils/with-intl";

const CLUSTERS = [
  {
    id: "cluster-rag",
    label: "RAG experience",
    category: "C" as const,
    gaps: ["RAG"],
    jd_skills: ["RAG"],
    jd_context: "The role leans on retrieval-augmented generation.",
  },
];

function jsonResponse(body: unknown, ok = true) {
  return {
    ok,
    status: ok ? 200 : 500,
    statusText: ok ? "OK" : "Error",
    json: async () => body,
  } as Response;
}

describe("LiabilityPanel", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it("renders nothing when there are no liabilities", () => {
    const { container } = render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[]}
          clusters={[]}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={() => {}}
        />
      )
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists each liability concept with both exits", () => {
    render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[{ concept: "RAG", evidence: "listed under Skills" }]}
          clusters={CLUSTERS}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={() => {}}
        />
      )
    );
    expect(screen.getByTestId("liability-card-RAG")).toHaveTextContent("RAG");
    expect(screen.getByTestId("liability-tell-story-RAG")).toBeInTheDocument();
    expect(screen.getByTestId("liability-drop-RAG")).toBeInTheDocument();
  });

  it("starting the story opens a targeted micro-session against the owning cluster", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ session_id: "sess-1", question: "Tell me about your RAG work.", choices: null })
    );
    render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[{ concept: "RAG" }]}
          clusters={CLUSTERS}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={() => {}}
        />
      )
    );
    fireEvent.click(screen.getByTestId("liability-tell-story-RAG"));

    await waitFor(() =>
      expect(screen.getByTestId("liability-question-RAG")).toHaveTextContent("Tell me about your RAG work.")
    );
    const [, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String((global.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0])).toBe("/api/session");
    const body = JSON.parse((options as RequestInit).body as string);
    expect(body).toEqual({ job_id: "job-1", mode: "targeted", target_gap: "cluster-rag" });
  });

  it("shows an unavailable note when no cluster has absorbed the concept yet", async () => {
    render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[{ concept: "RAG" }]}
          clusters={[]}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={() => {}}
        />
      )
    );
    fireEvent.click(screen.getByTestId("liability-tell-story-RAG"));
    expect(await screen.findByText(/preparing a question/i)).toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("submitting an answer sends it to the session and calls onStoryAdded", async () => {
    (global.fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(jsonResponse({ session_id: "sess-1", question: "Tell me about RAG." }))
      .mockResolvedValueOnce(jsonResponse({ complete: true }));
    const onStoryAdded = vi.fn();
    render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[{ concept: "RAG" }]}
          clusters={CLUSTERS}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={onStoryAdded}
        />
      )
    );
    fireEvent.click(screen.getByTestId("liability-tell-story-RAG"));
    await screen.findByTestId("liability-question-RAG");

    fireEvent.change(screen.getByTestId("liability-answer-textarea-RAG"), {
      target: { value: "Built a production RAG pipeline over the support corpus." },
    });
    fireEvent.click(screen.getByTestId("liability-submit-RAG"));

    await waitFor(() => expect(onStoryAdded).toHaveBeenCalledWith("RAG"));
    expect(screen.getByTestId("liability-resolved-RAG")).toBeInTheDocument();
    expect(String((global.fetch as ReturnType<typeof vi.fn>).mock.calls[1][0])).toBe("/api/session/sess-1/message");
  });

  it("dropping the keyword calls the downgrade endpoint, hides the card, and calls onDropped", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse({ id: "ga-1" }));
    const onDropped = vi.fn();
    render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[{ concept: "RAG" }]}
          clusters={CLUSTERS}
          apiBase=""
          onDropped={onDropped}
          onStoryAdded={() => {}}
        />
      )
    );
    fireEvent.click(screen.getByTestId("liability-drop-RAG"));

    await waitFor(() => expect(onDropped).toHaveBeenCalledWith("RAG"));
    expect(screen.queryByTestId("liability-card-RAG")).not.toBeInTheDocument();
    const [url, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toBe("/api/job/job-1/gaps/liabilities/downgrade");
    expect(JSON.parse((options as RequestInit).body as string)).toEqual({ concept: "RAG" });
  });

  it("shows an error and keeps the card when the drop request fails", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse({}, false));
    render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[{ concept: "RAG" }]}
          clusters={CLUSTERS}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={() => {}}
        />
      )
    );
    fireEvent.click(screen.getByTestId("liability-drop-RAG"));
    expect(await screen.findByText(/couldn't drop the keyword/i)).toBeInTheDocument();
    expect(screen.getByTestId("liability-card-RAG")).toBeInTheDocument();
  });
});
