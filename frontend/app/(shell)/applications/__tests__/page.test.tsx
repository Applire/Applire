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

// E041/US231 — cockpit page restructure. The detail page is now a zone
// composition: banners → header (identity, chips, actions, JD summary) →
// documents/journey zone stubs → tracking sidebar stub. The old stacked CRUD
// cards (Company & Role, Status Management, Details, Flow Progress, bottom Save)
// are GONE. This test locks the header action matrix (completed / mid-flow /
// no-flow), the removed cards, and the zone stubs.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ApplicationDetailPage from "../[appId]/page";
import { withIntl } from "@/lib/test-utils/with-intl";

const pushMock = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
  useParams: () => ({ appId: "app-1" }),
  usePathname: () => "/applications/app-1",
}));

const JOB = {
  id: "job-1",
  role_title: "Senior Software Engineer",
  seniority_level: "Senior",
  required_skills: ["Python", "FastAPI"],
  nice_to_have_skills: ["Kubernetes"],
  keywords: ["backend", "cloud"],
  company_culture_signals: ["remote-first"],
  language_requirement: "German",
  company_name: "TechVision GmbH",
};

const CV_LIST = [
  { cv_id: "cv-1", status: "ready", template: "classic_german", created_at: "2026-07-10T09:00:00Z" },
];

// Whole-branch review Finding 2: stale_cv is only populated when the profile
// changed after the newest CV — the header Re-tailor button also works on
// non-stale applications, where a fixed target_pages must still be forwarded
// (previously silently dropped, falling back to the region default).
const CV_LIST_WITH_TARGET_PAGES = [
  {
    cv_id: "cv-1",
    status: "ready",
    template: "classic_german",
    created_at: "2026-07-10T09:00:00Z",
    target_pages: 3,
  },
];

interface AppOverrides {
  workflow_status?: string;
  user_status?: string;
  flow_session_id?: string | null;
  flow_current_step?: string | null;
  source_url?: string | null;
  deadline?: string | null;
}

function baseApplication(o: AppOverrides = {}) {
  return {
    id: "app-1",
    job_analysis_id: "job-1",
    role_title: "Senior Software Engineer",
    company_name: "TechVision GmbH",
    workflow_status: o.workflow_status ?? "completed",
    user_status: o.user_status ?? "tracking",
    notes: null,
    applied_at: null,
    deadline: o.deadline ?? null,
    source_url: o.source_url ?? null,
    submitted_cv_id: null,
    submitted_cv_created_at: null,
    submitted_cover_letter_id: null,
    stale_cv: null,
    flow_session_id: o.flow_session_id === undefined ? "flow-1" : o.flow_session_id,
    flow_current_step: o.flow_current_step === undefined ? "complete" : o.flow_current_step,
    created_at: "2026-07-01T09:00:00Z",
    updated_at: "2026-07-10T09:00:00Z",
    expires_at: null,
  };
}

function mockFetch(opts: {
  app: ReturnType<typeof baseApplication>;
  hasCoverLetter?: boolean;
  cvList?: unknown[];
}) {
  global.fetch = vi.fn((input: string, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/cover-letter/by-job/")) {
      if (opts.hasCoverLetter) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            cover_letter_id: "cl-1",
            status: "ready",
            html_url: "/api/cover-letter/cl-1/html",
            pdf_url: "/api/cover-letter/cl-1/pdf",
            expires_at: "2026-08-01T09:00:00Z",
            letter_data: {},
          }),
        });
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: "not found" }) });
    }
    if (url.includes("/api/job/")) {
      return Promise.resolve({ ok: true, status: 200, json: async () => JOB });
    }
    if (url.includes("/api/cv?job_id=")) {
      return Promise.resolve({ ok: true, status: 200, json: async () => opts.cvList ?? CV_LIST });
    }
    if (url.includes("/api/cv/generate")) {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ cv_id: "cv-new" }) });
    }
    if (url.includes("/api/applications/") && url.includes("/start")) {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ flow_session_id: "flow-new" }) });
    }
    if (url.includes("/api/applications/")) {
      return Promise.resolve({ ok: true, status: 200, json: async () => opts.app });
    }
    return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
  }) as unknown as typeof fetch;
}

async function renderPage(opts: {
  app: ReturnType<typeof baseApplication>;
  hasCoverLetter?: boolean;
  cvList?: unknown[];
}) {
  mockFetch(opts);
  render(withIntl(<ApplicationDetailPage />));
  // Header identity confirms the load finished (scoped to the cockpit header —
  // AppTopbar renders the same role title as a breadcrumb).
  await waitFor(() =>
    expect(screen.getByTestId("dossier-header-title")).toHaveTextContent("Senior Software Engineer")
  );
}

describe("ApplicationDetailPage — cockpit header zone (US231)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders header identity: role title + company name", async () => {
    await renderPage({ app: baseApplication() });
    expect(screen.getByTestId("dossier-header-title")).toHaveTextContent("Senior Software Engineer");
    expect(screen.getByText("TechVision GmbH")).toBeInTheDocument();
  });

  it("completed state: Open CV + Re-tailor present, Resume + Start tailoring absent", async () => {
    await renderPage({
      app: baseApplication({ workflow_status: "completed", flow_current_step: "complete" }),
    });
    expect(screen.getByRole("button", { name: "Open CV" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-tailor" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start tailoring" })).not.toBeInTheDocument();
  });

  it("mid-flow state: Resume present, Open CV + Start tailoring absent", async () => {
    await renderPage({
      app: baseApplication({ workflow_status: "interviewing", flow_current_step: "interview" }),
    });
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open CV" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start tailoring" })).not.toBeInTheDocument();
  });

  it("no-flow state: Start tailoring present, Resume + Open CV + Re-tailor absent", async () => {
    await renderPage({
      app: baseApplication({
        workflow_status: "none",
        flow_session_id: null,
        flow_current_step: null,
      }),
    });
    expect(screen.getByRole("button", { name: "Start tailoring" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open CV" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Re-tailor" })).not.toBeInTheDocument();
  });

  it("Start tailoring navigates to the /flow/{id} INDEX, never a hard-coded step", async () => {
    await renderPage({
      app: baseApplication({
        workflow_status: "none",
        flow_session_id: null,
        flow_current_step: null,
      }),
    });
    fireEvent.click(screen.getByRole("button", { name: "Start tailoring" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/flow/flow-new"));
  });

  it("Cover letter action renders only when the by-job lookup returns one", async () => {
    await renderPage({ app: baseApplication(), hasCoverLetter: true });
    expect(screen.getByRole("button", { name: "Cover letter" })).toBeInTheDocument();
  });

  it("Cover letter action absent when the by-job lookup 404s", async () => {
    await renderPage({ app: baseApplication(), hasCoverLetter: false });
    expect(screen.queryByRole("button", { name: "Cover letter" })).not.toBeInTheDocument();
  });

  it("removes the old stacked CRUD cards", async () => {
    await renderPage({ app: baseApplication() });
    expect(screen.queryByText("Company & Role")).not.toBeInTheDocument();
    expect(screen.queryByText("Status Management")).not.toBeInTheDocument();
    expect(screen.queryByText("Details")).not.toBeInTheDocument();
    expect(screen.queryByText("Flow Progress")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save Changes" })).not.toBeInTheDocument();
  });

  it("renders the three zone stubs for 3.x to replace", async () => {
    await renderPage({ app: baseApplication() });
    expect(screen.getByTestId("dossier-documents-zone")).toBeInTheDocument();
    expect(screen.getByTestId("dossier-journey-zone")).toBeInTheDocument();
    expect(screen.getByTestId("dossier-tracking-sidebar")).toBeInTheDocument();
  });

  it("header Re-tailor forwards the newest ready CV's target_pages when stale_cv is null (Finding 2)", async () => {
    await renderPage({
      app: baseApplication(), // stale_cv: null — non-stale application
      cvList: CV_LIST_WITH_TARGET_PAGES,
    });
    fireEvent.click(screen.getByRole("button", { name: "Re-tailor" }));

    await waitFor(() => {
      const generateCall = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.find(([url]) =>
        String(url).includes("/api/cv/generate")
      );
      expect(generateCall).toBeTruthy();
      const body = JSON.parse((generateCall as [string, RequestInit])[1]!.body as string);
      expect(body.target_pages).toBe(3);
    });
  });
});
