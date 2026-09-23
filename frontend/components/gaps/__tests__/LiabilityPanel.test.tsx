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
import { LiabilityPanel, findOwningCluster } from "../LiabilityPanel";
import type { GapCluster } from "@/lib/match-utils";
import { withIntl } from "@/lib/test-utils/with-intl";

const CLUSTERS: GapCluster[] = [
  {
    id: "cluster-rag",
    label: "RAG experience",
    category: "C" as const,
    gaps: ["RAG"],
    jd_skills: ["RAG"],
    jd_context: "The role leans on retrieval-augmented generation.",
    outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
    coverage: "open",
    budget_remaining: 2,
    member_statuses: [{ member: "RAG", status: "gap" }],
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

/** A response carrying a real HTTP status (for the 409 refusal path), with a
 * working `clone()` — `tellStory`'s `refusalCodeOf` reads the body via
 * `res.clone().json()` so the plain `jsonResponse` stub (no `clone`) can't
 * stand in for it. */
function statusResponse(status: number, body: unknown): Response {
  const res = {
    ok: status < 400,
    status,
    statusText: status < 400 ? "OK" : "Error",
    json: async () => body,
  };
  return { ...res, clone: () => res } as unknown as Response;
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

// ---------------------------------------------------------------------------
// ADR-089 clause 3 — findOwningCluster searches ALL members (open gaps +
// outcome.covered + outcome.declined), never `gaps` alone.
// ---------------------------------------------------------------------------

describe("findOwningCluster", () => {
  const clusterDocker: GapCluster = {
    id: "cluster-docker",
    label: "Docker",
    category: "C",
    gaps: [],
    jd_skills: [],
    jd_context: "",
    outcome: { asked: 1, covered: ["Docker"], declined: [], session_ids: ["s1"] },
    coverage: "partly_covered",
    budget_remaining: 1,
    member_statuses: [{ member: "Docker", status: "covered" }],
  };
  const clusterScala: GapCluster = {
    id: "cluster-scala",
    label: "Scala",
    category: "C",
    gaps: [],
    jd_skills: [],
    jd_context: "",
    outcome: { asked: 1, covered: [], declined: ["Scala"], session_ids: ["s1"] },
    coverage: "declined",
    budget_remaining: 0,
    member_statuses: [{ member: "Scala", status: "declined" }],
  };

  it("finds a concept that sits only in outcome.covered", () => {
    expect(findOwningCluster("Docker", [clusterDocker, clusterScala])?.id).toBe("cluster-docker");
  });

  it("finds a concept that sits only in outcome.declined", () => {
    expect(findOwningCluster("Scala", [clusterDocker, clusterScala])?.id).toBe("cluster-scala");
  });
});

describe("LiabilityPanel — ADR-089 askability", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it("tellStory opens a session against the cluster that owns the concept only via outcome.covered", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ session_id: "sess-1", question: "Tell me more about Docker.", choices: null })
    );
    const clusters: GapCluster[] = [
      {
        id: "cluster-docker",
        label: "Docker",
        category: "C",
        gaps: ["Kubernetes"], // Docker itself is NOT in the open gaps list
        jd_skills: [],
        jd_context: "",
        outcome: { asked: 1, covered: ["Docker"], declined: [], session_ids: ["s1"] },
        coverage: "partly_covered",
        budget_remaining: 1,
        member_statuses: [
          { member: "Kubernetes", status: "gap" },
          { member: "Docker", status: "covered" },
        ],
      },
    ];
    render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[{ concept: "Docker" }]}
          clusters={clusters}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={() => {}}
        />
      )
    );
    fireEvent.click(screen.getByTestId("liability-tell-story-Docker"));

    await waitFor(() => expect(screen.getByTestId("liability-question-Docker")).toBeInTheDocument());
    const [, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const body = JSON.parse((options as RequestInit).body as string);
    expect(body.target_gap).toBe("cluster-docker");
  });

  it("pre-checks askability: a budget_remaining of 0 shows the budget-spent refusal without spending a fetch", async () => {
    const clusters: GapCluster[] = [
      {
        id: "cluster-rag",
        label: "RAG",
        category: "C",
        gaps: ["RAG"],
        jd_skills: [],
        jd_context: "",
        outcome: { asked: 2, covered: [], declined: [], session_ids: ["s1", "s2"] },
        coverage: "partly_covered",
        budget_remaining: 0,
        member_statuses: [{ member: "RAG", status: "gap" }],
      },
    ];
    render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[{ concept: "RAG" }]}
          clusters={clusters}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={() => {}}
        />
      )
    );
    fireEvent.click(screen.getByTestId("liability-tell-story-RAG"));

    expect(await screen.findByTestId("liability-refusal-RAG")).toHaveTextContent(
      "All questions on this gap have been asked already"
    );
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("pre-checks askability: an already-covered owning cluster shows the already-covered refusal without spending a fetch", async () => {
    const clusters: GapCluster[] = [
      {
        id: "cluster-rag",
        label: "RAG",
        category: "C",
        gaps: [],
        jd_skills: [],
        jd_context: "",
        outcome: { asked: 1, covered: ["RAG"], declined: [], session_ids: ["s1"] },
        coverage: "covered",
        budget_remaining: 1,
        member_statuses: [{ member: "RAG", status: "covered" }],
      },
    ];
    render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[{ concept: "RAG" }]}
          clusters={clusters}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={() => {}}
        />
      )
    );
    fireEvent.click(screen.getByTestId("liability-tell-story-RAG"));

    expect(await screen.findByTestId("liability-refusal-RAG")).toHaveTextContent("already covered");
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("a 409 gap_budget_spent from POST /api/session shows the refusal text instead of a question", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      statusResponse(409, { detail: { error_code: "gap_budget_spent", message: "no budget left" } })
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

    expect(await screen.findByTestId("liability-refusal-RAG")).toHaveTextContent(
      "All questions on this gap have been asked already"
    );
    expect(screen.queryByTestId("liability-question-RAG")).not.toBeInTheDocument();
  });
});

describe("LiabilityPanel — follow-up and confirmation turns (ADR-089 clause 2)", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it("a follow-up turn replaces the question, shows the follow-up label, clears the textarea, and calls onFollowUpTurn without resolving — a second complete turn then resolves", async () => {
    (global.fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(jsonResponse({ session_id: "sess-1", question: "Tell me about RAG." }))
      .mockResolvedValueOnce(
        jsonResponse({
          complete: false,
          question: "And Helm?",
          choices: ["a", "b"],
          cluster_coverage: {
            cluster_id: "cluster-rag",
            coverage: "partly_covered",
            open_concepts: ["Helm"],
            budget_remaining: 1,
          },
        })
      )
      .mockResolvedValueOnce(jsonResponse({ complete: true }));
    const onFollowUpTurn = vi.fn();
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
          onFollowUpTurn={onFollowUpTurn}
        />
      )
    );
    fireEvent.click(screen.getByTestId("liability-tell-story-RAG"));
    await screen.findByTestId("liability-question-RAG");

    fireEvent.change(screen.getByTestId("liability-answer-textarea-RAG"), {
      target: { value: "Built a production RAG pipeline." },
    });
    fireEvent.click(screen.getByTestId("liability-submit-RAG"));

    await waitFor(() =>
      expect(screen.getByTestId("liability-question-RAG")).toHaveTextContent("And Helm?")
    );
    expect(screen.getByTestId("liability-follow-up-label-RAG")).toHaveTextContent("Helm");
    expect((screen.getByTestId("liability-answer-textarea-RAG") as HTMLTextAreaElement).value).toBe("");
    expect(onFollowUpTurn).toHaveBeenCalledWith("RAG");
    expect(onStoryAdded).not.toHaveBeenCalled();
    expect(screen.queryByTestId("liability-resolved-RAG")).not.toBeInTheDocument();

    fireEvent.change(screen.getByTestId("liability-answer-textarea-RAG"), {
      target: { value: "Also used Helm for deploys." },
    });
    fireEvent.click(screen.getByTestId("liability-submit-RAG"));

    await waitFor(() => expect(onStoryAdded).toHaveBeenCalledWith("RAG"));
    expect(screen.getByTestId("liability-resolved-RAG")).toBeInTheDocument();
  });

  it("a confirmation turn (pending_confirmations) does not show the follow-up label", async () => {
    (global.fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(jsonResponse({ session_id: "sess-1", question: "Tell me about RAG." }))
      .mockResolvedValueOnce(
        jsonResponse({
          complete: false,
          question: "Same employer as before?",
          pending_confirmations: [{ question: "Same employer as before?", options: ["Yes", "No"] }],
          cluster_coverage: {
            cluster_id: "cluster-rag",
            coverage: "partly_covered",
            open_concepts: ["RAG"],
            budget_remaining: 1,
          },
        })
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
    await screen.findByTestId("liability-question-RAG");

    fireEvent.change(screen.getByTestId("liability-answer-textarea-RAG"), {
      target: { value: "Yes, same employer." },
    });
    fireEvent.click(screen.getByTestId("liability-submit-RAG"));

    await waitFor(() =>
      expect(screen.getByTestId("liability-question-RAG")).toHaveTextContent("Same employer as before?")
    );
    expect(screen.queryByTestId("liability-follow-up-label-RAG")).not.toBeInTheDocument();
  });
});

describe("LiabilityPanel — locking (ADR-089 clause 8, one open micro-session at a time)", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it("locked shows the locked hint and disables the tell-story button", () => {
    render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[{ concept: "RAG" }]}
          clusters={CLUSTERS}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={() => {}}
          locked
        />
      )
    );
    expect(screen.getByTestId("liability-locked-hint")).toBeInTheDocument();
    expect(screen.getByTestId("liability-tell-story-RAG")).toBeDisabled();
  });

  it("calls onActiveChange(true) once a session opens and onActiveChange(false) once the story resolves", async () => {
    (global.fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(jsonResponse({ session_id: "sess-1", question: "Tell me about RAG." }))
      .mockResolvedValueOnce(jsonResponse({ complete: true }));
    const onActiveChange = vi.fn();
    render(
      withIntl(
        <LiabilityPanel
          jobId="job-1"
          liabilities={[{ concept: "RAG" }]}
          clusters={CLUSTERS}
          apiBase=""
          onDropped={() => {}}
          onStoryAdded={() => {}}
          onActiveChange={onActiveChange}
        />
      )
    );
    expect(onActiveChange).toHaveBeenCalledWith(false);

    fireEvent.click(screen.getByTestId("liability-tell-story-RAG"));
    await waitFor(() => expect(onActiveChange).toHaveBeenCalledWith(true));

    await screen.findByTestId("liability-question-RAG");
    fireEvent.change(screen.getByTestId("liability-answer-textarea-RAG"), {
      target: { value: "Built a production RAG pipeline." },
    });
    fireEvent.click(screen.getByTestId("liability-submit-RAG"));

    await waitFor(() => expect(screen.getByTestId("liability-resolved-RAG")).toBeInTheDocument());
    expect(onActiveChange).toHaveBeenLastCalledWith(false);
  });
});
