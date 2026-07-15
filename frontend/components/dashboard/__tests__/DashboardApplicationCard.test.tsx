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

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DashboardApplicationCard } from "../DashboardApplicationCard";
import { patchApplicationStatus } from "@/lib/api/applications";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(",")}` : key,
}));

vi.mock("@/lib/api/applications", () => ({
  patchApplicationStatus: vi.fn().mockResolvedValue({ user_status: "interviewing" }),
}));

vi.mock("@/lib/profile-roles", () => ({
  markApplicationHired: vi.fn().mockResolvedValue({
    application_id: "a",
    user_status: "hired",
    redirect_url: "/profile/upload?action=add-role&source=application&application_id=a",
  }),
  addRole: vi.fn(),
  fetchOpenRoles: vi.fn(),
}));

const NOW = new Date().toISOString();
const STALE_48H = new Date(Date.now() - 49 * 36e5).toISOString();

function renderCard(overrides: Partial<React.ComponentProps<typeof DashboardApplicationCard>> = {}) {
  return render(
    <DashboardApplicationCard
      applicationId="app-1"
      roleTitle="Software Engineer"
      companyName="Acme GmbH"
      workflowStatus="analyzing"
      flowSessionId="flow-1"
      updatedAt={NOW}
      {...overrides}
    />
  );
}

describe("DashboardApplicationCard", () => {
  beforeEach(() => {
    mockPush.mockReset();
  });

  // ── Source link (E039/US216 — dossier) ───────────────────────────────────

  it("renders the source link as an external anchor when sourceUrl is set", () => {
    renderCard({ sourceUrl: "https://jobs.example.com/123" });
    const link = screen.getByRole("link", { name: "sourceLinkLabel" });
    expect(link).toHaveAttribute("href", "https://jobs.example.com/123");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  // ── Sent badge (E039/US219 — pinned submitted version) ───────────────────

  it("shows the sent badge with the pinned version's date (US219)", () => {
    renderCard({
      submittedCvId: "cv-9",
      submittedCvCreatedAt: "2026-07-05T10:00:00Z",
    });
    const badge = screen.getByTestId("sent-badge");
    // Version identity = the pin's creation date, rendered via the sentBadge key
    expect(badge).toHaveTextContent(
      `sentBadge:${new Date("2026-07-05T10:00:00Z").toLocaleDateString()}`,
    );
  });

  it("shows no sent badge when nothing is pinned", () => {
    renderCard();
    expect(screen.queryByTestId("sent-badge")).toBeNull();
  });

  // ── Stale-CV indicator (E039/US221 — journey Branch H) ───────────────────

  it("shows the profile-grew badge when the backend flags the CV as stale", () => {
    renderCard({
      staleCv: {
        latest_cv_id: "cv-1",
        latest_cv_created_at: "2026-07-01T10:00:00Z",
        latest_cv_template: "classic_german",
        profile_enriched_at: "2026-07-08T10:00:00Z",
        gained: [{ section: "skills", count: 3 }],
      },
    });
    expect(screen.getByTestId("stale-cv-badge")).toHaveTextContent("staleCvBadge");
  });

  it("shows no stale badge when the hint is absent", () => {
    renderCard();
    expect(screen.queryByTestId("stale-cv-badge")).toBeNull();
  });

  it("renders no source link when sourceUrl is absent", () => {
    renderCard();
    expect(screen.queryByRole("link", { name: "sourceLinkLabel" })).not.toBeInTheDocument();
  });

  it("clicking the source link does not navigate to the application detail", () => {
    renderCard({ sourceUrl: "https://jobs.example.com/123" });
    fireEvent.click(screen.getByRole("link", { name: "sourceLinkLabel" }));
    expect(mockPush).not.toHaveBeenCalled();
  });

  // ── Status derivation ────────────────────────────────────────────────────

  it("shows 'CV Ready' chip for completed workflow", () => {
    renderCard({ workflowStatus: "completed" });
    expect(screen.getByText("chipCvReady")).toBeInTheDocument();
  });

  it("shows 'Tracking' chip for none workflow", () => {
    renderCard({ workflowStatus: "none" });
    expect(screen.getByText("chipTracking")).toBeInTheDocument();
  });

  it("shows 'In Progress' chip for recent analyzing status", () => {
    renderCard({ workflowStatus: "analyzing", updatedAt: NOW });
    expect(screen.getByText("chipInProgress")).toBeInTheDocument();
  });

  it("shows 'Interrupted' chip for stale analyzing status (>48h old)", () => {
    renderCard({ workflowStatus: "analyzing", updatedAt: STALE_48H });
    expect(screen.getByText("chipInterrupted")).toBeInTheDocument();
  });

  it("shows 'In Progress' chip for recent cv_generating status", () => {
    renderCard({ workflowStatus: "cv_generating", updatedAt: NOW });
    expect(screen.getByText("chipInProgress")).toBeInTheDocument();
  });

  // ── Action button labels (E041/US235 — button opens the dossier, same as
  // the card body, so every flow-session status now reads "Open") ─────────

  it("action button shows 'Open' for cv_ready", () => {
    renderCard({ workflowStatus: "completed" });
    expect(screen.getByRole("button", { name: /open/i })).toBeInTheDocument();
  });

  it("action button shows 'Start Flow' for tracking", () => {
    renderCard({ workflowStatus: "none" });
    expect(screen.getByRole("button", { name: /actionStartFlow/i })).toBeInTheDocument();
  });

  it("action button shows 'Open' for in_progress", () => {
    renderCard({ workflowStatus: "analyzing", updatedAt: NOW });
    expect(screen.getByRole("button", { name: /open/i })).toBeInTheDocument();
  });

  it("action button shows 'Open' for interrupted", () => {
    renderCard({ workflowStatus: "analyzing", updatedAt: STALE_48H });
    expect(screen.getByRole("button", { name: /open/i })).toBeInTheDocument();
  });

  // ── Card click routing ───────────────────────────────────────────────────

  it("clicking the card navigates to /applications/{id}", () => {
    renderCard();
    // Click the role title text — bubbles up to the card's onClick handler
    fireEvent.click(screen.getByText("Software Engineer"));
    expect(mockPush).toHaveBeenCalledWith("/applications/app-1");
  });

  // ── Action button routing (E041/US235) ───────────────────────────────────

  // The button now routes into the dossier, exactly like the card body — the
  // dossier header itself offers Resume (mid-flow) / Open CV (completed), so
  // no capability is lost by no longer hard-coding a flow step here.

  it("Open button (cv_ready) navigates to /applications/{id}", () => {
    renderCard({ workflowStatus: "completed", flowSessionId: "flow-99" });
    fireEvent.click(screen.getByRole("button", { name: /open/i }));
    expect(mockPush).toHaveBeenCalledWith("/applications/app-1");
  });

  it("Open button (in_progress) navigates to /applications/{id}", () => {
    renderCard({ workflowStatus: "analyzing", updatedAt: NOW, flowSessionId: "flow-42" });
    fireEvent.click(screen.getByRole("button", { name: /open/i }));
    expect(mockPush).toHaveBeenCalledWith("/applications/app-1");
  });

  it("Open button (interrupted) navigates to /applications/{id}", () => {
    renderCard({ workflowStatus: "analyzing", updatedAt: STALE_48H, flowSessionId: "flow-7" });
    fireEvent.click(screen.getByRole("button", { name: /open/i }));
    expect(mockPush).toHaveBeenCalledWith("/applications/app-1");
  });

  it("Start Flow button calls onStartFlow callback", () => {
    const onStartFlow = vi.fn();
    renderCard({ workflowStatus: "none", onStartFlow });
    fireEvent.click(screen.getByRole("button", { name: /actionStartFlow/i }));
    expect(onStartFlow).toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
  });

  // ── Action button testid gating (E041/US235 — expanded to ALL flow-session
  // statuses; PQ selectors depend on this) ─────────────────────────────────

  it("shows the open-btn testid for cv_ready, in_progress, and interrupted", () => {
    const { unmount: unmountCvReady } = renderCard({ workflowStatus: "completed" });
    expect(screen.getByTestId("dashboard-card-open-btn")).toBeInTheDocument();
    unmountCvReady();

    const { unmount: unmountInProgress } = renderCard({ workflowStatus: "analyzing", updatedAt: NOW });
    expect(screen.getByTestId("dashboard-card-open-btn")).toBeInTheDocument();
    unmountInProgress();

    renderCard({ workflowStatus: "analyzing", updatedAt: STALE_48H });
    expect(screen.getByTestId("dashboard-card-open-btn")).toBeInTheDocument();
  });

  it("omits the open-btn testid on tracking cards", () => {
    renderCard({ workflowStatus: "none" });
    expect(screen.queryByTestId("dashboard-card-open-btn")).toBeNull();
  });

  // ── Display fields ───────────────────────────────────────────────────────

  it("renders role title and company name", () => {
    renderCard();
    expect(screen.getByText("Software Engineer")).toBeInTheDocument();
    expect(screen.getByText("Acme GmbH")).toBeInTheDocument();
  });

  it("uses companyName initial for avatar when company provided", () => {
    renderCard({ companyName: "Zara" });
    expect(screen.getByText("Z")).toBeInTheDocument();
  });

  it("falls back to roleTitle initial when companyName is null", () => {
    renderCard({ companyName: null, roleTitle: "Manager" });
    expect(screen.getByText("M")).toBeInTheDocument();
  });

  it("renders fallback text when roleTitle is null", () => {
    renderCard({ roleTitle: null, companyName: null });
    expect(screen.getByText("unknownRole")).toBeInTheDocument();
  });

  // ── Status pipeline control (E039/US218) ─────────────────────────────────

  it("renders a status select with the current user status", () => {
    renderCard({ userStatus: "applied" });
    const select = screen.getByRole("combobox", { name: "statusSelectLabel" });
    expect(select).toHaveValue("applied");
  });

  it("defaults the status select to tracking when userStatus is absent", () => {
    renderCard();
    expect(screen.getByRole("combobox", { name: "statusSelectLabel" })).toHaveValue("tracking");
  });

  it("offers the full pipeline including cancelled (US222)", () => {
    renderCard();
    const select = screen.getByRole("combobox", { name: "statusSelectLabel" });
    const values = Array.from(select.querySelectorAll("option")).map((o) => o.getAttribute("value"));
    expect(values).toEqual([
      "tracking", "applied", "interviewing", "offer", "rejected", "hired", "cancelled",
    ]);
  });

  it("changing the status PATCHes the application and notifies the parent", async () => {
    const onStatusChange = vi.fn();
    renderCard({ userStatus: "applied", onStatusChange });
    fireEvent.change(screen.getByRole("combobox", { name: "statusSelectLabel" }), {
      target: { value: "interviewing" },
    });
    await waitFor(() => expect(onStatusChange).toHaveBeenCalledWith("interviewing"));
    expect(patchApplicationStatus).toHaveBeenCalledWith("app-1", "interviewing");
  });

  it("clicking the status select does not navigate to the detail page", () => {
    renderCard();
    fireEvent.click(screen.getByRole("combobox", { name: "statusSelectLabel" }));
    expect(mockPush).not.toHaveBeenCalled();
  });

  // ── Mark as Hired affordance ─────────────────────────────────────────────

  it("shows when workflow_status=completed and user_status!=hired", () => {
    renderCard({ workflowStatus: "completed", userStatus: "applied" });
    expect(screen.getByRole("button", { name: /^button$/i })).toBeInTheDocument();
  });

  it("hides when user_status=hired", () => {
    renderCard({ workflowStatus: "completed", userStatus: "hired" });
    expect(screen.queryByRole("button", { name: /^button$/i })).not.toBeInTheDocument();
  });

  it("hides when CV is not yet ready", () => {
    renderCard({ workflowStatus: "analyzing", userStatus: "applied" });
    expect(screen.queryByRole("button", { name: /^button$/i })).not.toBeInTheDocument();
  });
});
