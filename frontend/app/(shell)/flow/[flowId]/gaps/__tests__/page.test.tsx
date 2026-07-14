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
import { render, screen, waitFor } from "@testing-library/react";
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
