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
 * Gaps page context cases (Spaghettieis UAT follow-up-flow findings):
 *
 *   Case 1 — JD + CVs (first run, user_type "new"):       hero + merge pointer
 *            (only if THIS run merged) + gaps + interview offer.
 *   Case 2 — CVs only (no job on the flow):               profile summary +
 *            merge pointer; no score / gaps / interview / generate.
 *   Case 3 — JD only (follow-up, user_type "returning"):  no hero, no merge
 *            pointer; gaps if they exist; interview OFFERED because gaps
 *            exist (gap-driven, ADR-016 amended 2026-07-13).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import GapsPage from "../page";

const mockPush = vi.fn();
const mockReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("next-intl", () => ({
  useTranslations: (ns: string) => (key: string, _vars?: object) => `${ns}.${key}`,
}));

function fulfilledParams(flowId: string) {
  const p = Promise.resolve({ flowId });
  return Object.assign(p, { status: "fulfilled", value: { flowId } });
}

const FLOW_CREATED_AT = "2026-07-13T10:00:00Z";

interface ApiConfig {
  userType: "new" | "returning";
  jobId: string | null;
  categoryB?: string[];
  categoryC?: string[];
  clusters?: object[];
  changes?: object;
}

function mockApis(cfg: ApiConfig) {
  const gaps = {
    id: "ga1",
    match_score: 0.5,
    category_a: ["Python"],
    category_b: cfg.categoryB ?? [],
    category_c: cfg.categoryC ?? [],
    strengths: [],
    gap_clusters:
      cfg.clusters ??
      (cfg.categoryC ?? []).map((g, i) => ({
        id: `cl-${i}`,
        label: g,
        category: "C",
        gaps: [g],
        jd_skills: [g],
        jd_context: `Context for ${g}`,
      })),
    keyword_ledger: [],
  };
  global.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/flow/f1/state")) {
      return {
        ok: true,
        json: async () => ({
          flow_id: "f1",
          job_id: cfg.jobId,
          user_type: cfg.userType,
          current_step: "gap_analysis",
          available_actions: {},
          job_summary: cfg.jobId ? { role_title: "Engineer" } : null,
          application_id: null,
          created_at: FLOW_CREATED_AT,
        }),
      } as Response;
    }
    if (cfg.jobId && url.endsWith(`/api/job/${cfg.jobId}`)) {
      return {
        ok: true,
        json: async () => ({
          role_title: "Engineer",
          company_name: "Acme",
          required_skills: [],
          nice_to_have_skills: [],
        }),
      } as Response;
    }
    if (cfg.jobId && url.includes(`/api/job/${cfg.jobId}/gaps`)) {
      return { ok: true, json: async () => gaps } as Response;
    }
    if (url.includes("/api/profile/changes")) {
      return {
        ok: true,
        json: async () => cfg.changes ?? { enrichment_history: [], pending_conflicts: [] },
      } as Response;
    }
    if (url.includes("/api/profile")) {
      return {
        ok: true,
        json: async () => ({
          stats: { positions: 2, projects: 1, certifications: 0, data_points: 17 },
        }),
      } as Response;
    }
    return { ok: false, status: 404, json: async () => ({}) } as Response;
  }) as unknown as typeof fetch;
}

function mergedChanges(timestamp: string) {
  return {
    enrichment_history: [
      {
        source: "cv_upload",
        timestamp,
        changes: [
          { section: "skills", field: "skills", action: "merged", new_value: "X" },
        ],
      },
    ],
    pending_conflicts: [],
  };
}

async function renderPage() {
  render(<GapsPage params={fulfilledParams("f1")} />);
  await waitFor(() =>
    expect(screen.getByTestId("gap-analysis-page")).toBeInTheDocument(),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  mockPush.mockReset();
});

describe("Case 3 — JD-only follow-up (returning + job)", () => {
  it("hides the onboarding hero and the merge pointer, even with an old merge in the trail", async () => {
    mockApis({
      userType: "returning",
      jobId: "j1",
      categoryC: ["Kubernetes"],
      changes: mergedChanges("2026-07-01T00:00:00Z"),
    });
    await renderPage();

    expect(screen.queryByText("gaps.masterProfileCreated")).not.toBeInTheDocument();
    expect(screen.queryByText("gaps.masterProfileUpdated")).not.toBeInTheDocument();
    expect(screen.queryByTestId("profile-review-section")).not.toBeInTheDocument();
  });

  it("offers the interview when gaps exist (gap-driven, not user-type-driven)", async () => {
    mockApis({ userType: "returning", jobId: "j1", categoryC: ["Kubernetes"] });
    await renderPage();

    expect(screen.getByTestId("gaps-section")).toBeInTheDocument();
    expect(screen.getByTestId("interview-button")).toBeInTheDocument();
    expect(screen.getByTestId("generate-cv-button")).toBeInTheDocument();
  });

  it("goes straight to generation when no gaps exist — no interview button", async () => {
    mockApis({ userType: "returning", jobId: "j1" });
    await renderPage();

    expect(screen.queryByTestId("gaps-section")).not.toBeInTheDocument();
    expect(screen.queryByTestId("interview-button")).not.toBeInTheDocument();
    expect(screen.getByTestId("generate-cv-button")).toBeInTheDocument();
  });
});

describe("Case 1 — first run (new + job)", () => {
  it("shows the hero and the merge pointer when THIS run merged", async () => {
    mockApis({
      userType: "new",
      jobId: "j1",
      categoryC: ["Kubernetes"],
      changes: mergedChanges("2026-07-13T10:00:30Z"),
    });
    await renderPage();

    expect(screen.getByText("gaps.masterProfileCreated")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("profile-review-section")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("interview-button")).toBeInTheDocument();
  });

  it("hides the merge pointer when the only merge predates this flow", async () => {
    mockApis({
      userType: "new",
      jobId: "j1",
      categoryC: ["Kubernetes"],
      changes: mergedChanges("2026-07-01T00:00:00Z"),
    });
    await renderPage();

    expect(screen.getByText("gaps.masterProfileCreated")).toBeInTheDocument();
    expect(screen.queryByTestId("profile-review-section")).not.toBeInTheDocument();
  });
});

describe("Case 2 — CV-only ingestion (no job on the flow)", () => {
  it("shows the profile summary without score/gaps/interview/generate — and no error", async () => {
    mockApis({
      userType: "returning",
      jobId: null,
      changes: mergedChanges("2026-07-13T10:00:30Z"),
    });
    await renderPage();

    expect(screen.getByText("gaps.masterProfileUpdated")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("profile-review-section")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("error-message")).not.toBeInTheDocument();
    expect(screen.queryByTestId("match-score-display")).not.toBeInTheDocument();
    expect(screen.queryByTestId("gaps-section")).not.toBeInTheDocument();
    expect(screen.queryByTestId("interview-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("generate-cv-button")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// ADR-089 clause 8 — the gaps page renders server truth
// ---------------------------------------------------------------------------

type Json = Record<string, unknown>;

function outcome(asked = 0, covered: string[] = [], declined: string[] = []) {
  return { asked, covered, declined, session_ids: asked ? ["s-old"] : [] };
}

function cl(id: string, over: Json = {}): Json {
  return {
    id,
    label: `Label ${id}`,
    category: "C",
    gaps: [`${id}-a`, `${id}-b`],
    jd_skills: [],
    jd_context: "",
    outcome: outcome(),
    coverage: "open",
    budget_remaining: 2,
    ...over,
  };
}

function analysis(clusters: Json[], over: Json = {}): Json {
  return {
    id: "ga1",
    match_score: 0.5,
    category_a: ["Python"],
    category_b: [],
    category_c: clusters.flatMap((c) => (c.gaps as string[]) ?? []),
    strengths: [],
    gap_clusters: clusters,
    keyword_ledger: [],
    ...over,
  };
}

interface Server {
  row: Json;
  /** POST /api/session — a body, or a {status, body} pair. */
  session?: (body: Json) => { status: number; body: Json };
  /** POST /api/session/{id}/message — one response per turn, in order. */
  turns?: Json[];
  /** POST /gaps/refresh — the recomputed row. */
  refreshed?: Json;
  /** Mutation the message turn applies to the row (the turn's own record). */
  onTurn?: (row: Json, turnIndex: number) => Json;
}

function serve(server: Server) {
  let turn = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const ok = (body: unknown, status = 200) =>
      ({ ok: status < 400, status, statusText: "", json: async () => body, clone() { return this; } }) as unknown as Response;
    if (url.includes("/api/flow/f1/state")) {
      return ok({
        flow_id: "f1",
        job_id: "j1",
        user_type: "returning",
        current_step: "gap_analysis",
        available_actions: {},
        job_summary: { role_title: "Engineer" },
        application_id: null,
        created_at: FLOW_CREATED_AT,
      });
    }
    if (url.endsWith("/api/job/j1/gaps/refresh") && method === "POST") {
      server.row = server.refreshed ?? server.row;
      return ok(server.row);
    }
    if (url.endsWith("/api/job/j1/gaps")) return ok(server.row);
    if (url.endsWith("/api/job/j1")) return ok({ role_title: "Engineer", required_skills: [], nice_to_have_skills: [] });
    if (url.endsWith("/api/session") && method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const r = server.session
        ? server.session(body)
        : { status: 200, body: { session_id: "s1", question: "Q1?", choices: null } };
      return ok(r.body, r.status);
    }
    if (/\/api\/session\/[^/]+\/message$/.test(url)) {
      const body = server.turns?.[turn] ?? { complete: true };
      if (server.onTurn) server.row = server.onTurn(server.row, turn);
      turn += 1;
      return ok(body);
    }
    if (url.includes("/api/profile/changes")) return ok({ enrichment_history: [], pending_conflicts: [] });
    if (url.includes("/api/profile/health")) return ok({ issues: [] });
    if (url.includes("/api/profile")) return ok({ stats: {} });
    return ok({}, 404);
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

function card(id: string): HTMLElement {
  const el = document.querySelector(`[data-cluster-id="${id}"]`);
  if (!el) throw new Error(`no card ${id}`);
  return el as HTMLElement;
}

function calls(fetchMock: ReturnType<typeof vi.fn>, pred: (url: string, method: string) => boolean) {
  return fetchMock.mock.calls.filter(([u, init]) => pred(String(u), (init as RequestInit | undefined)?.method ?? "GET"));
}

async function answer(id: string, text: string) {
  fireEvent.change(within(card(id)).getByTestId("gap-answer-textarea"), { target: { value: text } });
  fireEvent.click(within(card(id)).getByTestId("gap-submit-button"));
}

describe("ADR-089 cl. 8 — card state is the server's record", () => {
  it("renders all four coverage states and a spent budget straight from the row on load (reload persistence)", async () => {
    serve({
      row: analysis([
        cl("open"),
        cl("partly", { gaps: ["Terraform"], outcome: outcome(1, ["Ansible"]), coverage: "partly_covered", budget_remaining: 1 }),
        cl("covered", { gaps: [], outcome: outcome(1, ["Jenkins"]), coverage: "covered", budget_remaining: 1 }),
        cl("declined", { gaps: [], outcome: outcome(1, [], ["Go"]), coverage: "declined", budget_remaining: 1 }),
        cl("spent", { gaps: ["Prometheus"], outcome: outcome(2, ["Grafana"]), coverage: "partly_covered", budget_remaining: 0 }),
      ]),
    });
    await renderPage();
    await waitFor(() => expect(card("open")).toBeInTheDocument());

    expect(card("open")).toHaveAttribute("data-coverage", "open");
    expect(card("partly")).toHaveAttribute("data-coverage", "partly_covered");
    expect(within(card("partly")).getByTestId("gap-partly-covered")).toBeInTheDocument();
    expect(within(card("covered")).getByTestId("gap-resolved")).toBeInTheDocument();
    expect(within(card("declined")).getByTestId("gap-declined")).toBeInTheDocument();
    expect(within(card("spent")).getByTestId("gap-budget-spent")).toBeInTheDocument();

    // Clickable exactly when askable.
    for (const id of ["open", "partly"]) expect(card(id).className).toContain("cursor-pointer");
    for (const id of ["covered", "declined", "spent"]) expect(card(id).className).not.toContain("cursor-pointer");

    // Badge + "to address" count from the record, not from clicks.
    expect(screen.getByTestId("covered-badge")).toHaveTextContent("gaps.resolvedBadge");
    expect(screen.getByText("gaps.clustersToAddress")).toBeInTheDocument();
  });

  it("keeps the gaps section (with the worked cards) when every cluster is finished", async () => {
    serve({
      row: analysis(
        [cl("covered", { gaps: [], outcome: outcome(1, ["Jenkins"]), coverage: "covered" })],
        { category_c: [], category_b: [] },
      ),
    });
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("gaps-section")).toBeInTheDocument());
    expect(within(card("covered")).getByTestId("gap-resolved")).toBeInTheDocument();
  });
});

describe("ADR-089 cl. 8 — the answer turn's response is read", () => {
  it("complete=false: the follow-up question and choices appear inline in the SAME card; no refresh; the row is re-read", async () => {
    const fetchMock = serve({
      row: analysis([cl("k8s", { gaps: ["Kubernetes", "Helm"] }), cl("iac")]),
      turns: [
        {
          complete: false,
          question: "And Helm — own charts?",
          choices: ["Own charts", "Adapted charts"],
          cluster_coverage: { cluster_id: "k8s", coverage: "partly_covered", open_concepts: ["Helm"], budget_remaining: 1 },
        },
      ],
      onTurn: (row) => ({
        ...row,
        gap_clusters: (row.gap_clusters as Json[]).map((c) =>
          c.id === "k8s"
            ? { ...c, gaps: ["Helm"], outcome: outcome(1, ["Kubernetes"]), coverage: "partly_covered", budget_remaining: 1 }
            : c,
        ),
      }),
    });
    await renderPage();
    fireEvent.click(await waitFor(() => card("k8s")));
    await waitFor(() => expect(within(card("k8s")).getByTestId("gap-question")).toHaveTextContent("Q1?"));

    await answer("k8s", "Ran a 40-service cluster for three years.");

    await waitFor(() =>
      expect(within(card("k8s")).getByTestId("gap-question")).toHaveTextContent("And Helm — own charts?"),
    );
    expect(within(card("k8s")).getByTestId("gap-follow-up-label")).toBeInTheDocument();
    expect(within(card("k8s")).getAllByTestId("gap-choice").map((b) => b.textContent)).toEqual([
      "Own charts",
      "Adapted charts",
    ]);
    expect(within(card("k8s")).getByTestId("gap-answer-textarea")).toHaveValue("");
    // Not green: the card shows the partial record, Kubernetes covered, Helm open.
    await waitFor(() => expect(card("k8s")).toHaveAttribute("data-coverage", "partly_covered"));
    const chips = within(card("k8s")).getAllByTestId("gap-member");
    // (the sr-only state label is stripped — it is asserted by the card's own tests)
    const term = (c: HTMLElement) => (c.textContent ?? "").replace(/gaps\.\w+/g, "");
    expect(chips.map((c) => [term(c), c.getAttribute("data-state")])).toEqual([
      ["Helm", "open"],
      ["Kubernetes", "covered"],
    ]);
    expect(within(card("k8s")).queryByTestId("gap-resolved")).not.toBeInTheDocument();
    // No refresh while the micro-session is still open.
    expect(calls(fetchMock, (u, m) => u.endsWith("/gaps/refresh") && m === "POST")).toHaveLength(0);
    // The decisions stack re-reads the durable health surface after the turn.
    expect(calls(fetchMock, (u) => u.includes("/api/profile/health")).length).toBeGreaterThanOrEqual(2);
  });

  it("complete=true: POST /gaps/refresh and the WHOLE analysis is replaced — new cluster list, new score", async () => {
    const fetchMock = serve({
      row: analysis([cl("k8s"), cl("iac")], { match_score: 0.5 }),
      turns: [{ complete: true, cluster_coverage: { cluster_id: "k8s", coverage: "covered", open_concepts: [], budget_remaining: 1 } }],
      refreshed: analysis(
        [
          cl("k8s", { gaps: [], outcome: outcome(1, ["k8s-a", "k8s-b"]), coverage: "covered", budget_remaining: 1 }),
          cl("iac"),
          cl("new-one", { label: "Appended cluster" }),
        ],
        { match_score: 0.64 },
      ),
    });
    await renderPage();
    fireEvent.click(await waitFor(() => card("k8s")));
    await waitFor(() => within(card("k8s")).getByTestId("gap-answer-textarea"));
    await answer("k8s", "Yes, three years of it.");

    await waitFor(() => expect(within(card("k8s")).getByTestId("gap-resolved")).toBeInTheDocument());
    expect(card("new-one")).toBeInTheDocument(); // the list itself was replaced, not only the score
    expect(calls(fetchMock, (u, m) => u.endsWith("/api/job/j1/gaps/refresh") && m === "POST")).toHaveLength(1);
    await waitFor(() =>
      expect(screen.getByTestId("match-score-display")).toHaveTextContent("gaps.matchScoreDisplay"),
    );
    expect(within(card("k8s")).queryByTestId("gap-question")).not.toBeInTheDocument();
  });

  it("a failed refresh falls back to re-reading the latest row", async () => {
    let refreshCalls = 0;
    const server: Server = {
      row: analysis([cl("k8s")]),
      turns: [{ complete: true }],
      onTurn: (row) => ({
        ...row,
        gap_clusters: [cl("k8s", { gaps: [], outcome: outcome(1, ["k8s-a", "k8s-b"]), coverage: "covered" })],
      }),
    };
    const fetchMock = serve(server);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/gaps/refresh")) {
        refreshCalls += 1;
        return { ok: false, status: 504, statusText: "", json: async () => ({}) } as Response;
      }
      return base(input, init);
    });
    await renderPage();
    fireEvent.click(await waitFor(() => card("k8s")));
    await waitFor(() => within(card("k8s")).getByTestId("gap-answer-textarea"));
    await answer("k8s", "Yes.");
    await waitFor(() => expect(within(card("k8s")).getByTestId("gap-resolved")).toBeInTheDocument());
    expect(refreshCalls).toBe(1);
  });
});

describe("ADR-089 cl. 8 — one open micro-session per page", () => {
  it("while one card holds a session, no other card is clickable and the lock hint shows; cancel releases it", async () => {
    const fetchMock = serve({ row: analysis([cl("a"), cl("b")]) });
    await renderPage();
    fireEvent.click(await waitFor(() => card("a")));
    await waitFor(() => within(card("a")).getByTestId("gap-question"));

    expect(screen.getByTestId("gaps-locked-hint")).toBeInTheDocument();
    expect(card("b").className).not.toContain("cursor-pointer");
    expect(card("b").className).toContain("opacity-60");
    fireEvent.click(card("b"));
    expect(calls(fetchMock, (u, m) => u.endsWith("/api/session") && m === "POST")).toHaveLength(1);

    fireEvent.click(within(card("a")).getByText("common.cancel"));
    await waitFor(() => expect(card("b").className).toContain("cursor-pointer"));
    expect(screen.queryByTestId("gaps-locked-hint")).not.toBeInTheDocument();
  });

  it("a double click on one card opens exactly one session", async () => {
    const fetchMock = serve({ row: analysis([cl("a")]) });
    await renderPage();
    const target = await waitFor(() => card("a"));
    fireEvent.click(target);
    fireEvent.click(target);
    await waitFor(() => within(card("a")).getByTestId("gap-question"));
    expect(calls(fetchMock, (u, m) => u.endsWith("/api/session") && m === "POST")).toHaveLength(1);
  });
});

describe("ADR-089 contract item 5 — a 409 refusal is said inline", () => {
  for (const [code, key] of [
    ["gap_budget_spent", "gaps.refusalBudgetSpent"],
    ["gap_already_covered", "gaps.refusalAlreadyCovered"],
  ] as const) {
    it(`${code}: inline message on the card, no question, row re-read`, async () => {
      const fetchMock = serve({
        row: analysis([cl("a")]),
        session: () => ({ status: 409, body: { detail: { error_code: code, message: "server words" } } }),
      });
      await renderPage();
      fireEvent.click(await waitFor(() => card("a")));
      await waitFor(() => expect(within(card("a")).getByTestId("gap-refusal")).toHaveTextContent(key));
      expect(within(card("a")).queryByTestId("gap-question")).not.toBeInTheDocument();
      expect(screen.queryByText("server words")).not.toBeInTheDocument();
      // Not clickable again after the server said no.
      expect(card("a").className).not.toContain("cursor-pointer");
      // The row behind the refusal is re-read (GET, never a recompute).
      await waitFor(() =>
        expect(calls(fetchMock, (u, m) => u.endsWith("/api/job/j1/gaps") && m === "GET").length).toBeGreaterThanOrEqual(2),
      );
      expect(calls(fetchMock, (u) => u.endsWith("/gaps/refresh"))).toHaveLength(0);
    });
  }
});
