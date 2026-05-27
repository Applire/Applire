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
import { render, screen, fireEvent } from "@testing-library/react";
import { DashboardApplicationCard } from "../DashboardApplicationCard";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
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

  // ── Action button labels ─────────────────────────────────────────────────

  it("action button shows 'Open' for cv_ready", () => {
    renderCard({ workflowStatus: "completed" });
    expect(screen.getByRole("button", { name: /open/i })).toBeInTheDocument();
  });

  it("action button shows 'Start Flow' for tracking", () => {
    renderCard({ workflowStatus: "none" });
    expect(screen.getByRole("button", { name: /actionStartFlow/i })).toBeInTheDocument();
  });

  it("action button shows 'Resume' for in_progress", () => {
    renderCard({ workflowStatus: "analyzing", updatedAt: NOW });
    expect(screen.getByRole("button", { name: /resume/i })).toBeInTheDocument();
  });

  it("action button shows 'Continue' for interrupted", () => {
    renderCard({ workflowStatus: "analyzing", updatedAt: STALE_48H });
    expect(screen.getByRole("button", { name: /continue/i })).toBeInTheDocument();
  });

  // ── Card click routing ───────────────────────────────────────────────────

  it("clicking the card navigates to /applications/{id}", () => {
    renderCard();
    // Click the role title text — bubbles up to the card's onClick handler
    fireEvent.click(screen.getByText("Software Engineer"));
    expect(mockPush).toHaveBeenCalledWith("/applications/app-1");
  });

  // ── Action button routing ────────────────────────────────────────────────

  it("Open button navigates to /flow/{flowSessionId}/cv", () => {
    renderCard({ workflowStatus: "completed", flowSessionId: "flow-99" });
    fireEvent.click(screen.getByRole("button", { name: /open/i }));
    expect(mockPush).toHaveBeenCalledWith("/flow/flow-99/cv");
  });

  it("Resume button navigates to /flow/{flowSessionId}/interview", () => {
    renderCard({ workflowStatus: "analyzing", updatedAt: NOW, flowSessionId: "flow-42" });
    fireEvent.click(screen.getByRole("button", { name: /resume/i }));
    expect(mockPush).toHaveBeenCalledWith("/flow/flow-42/interview");
  });

  it("Start Flow button calls onStartFlow callback", () => {
    const onStartFlow = vi.fn();
    renderCard({ workflowStatus: "none", onStartFlow });
    fireEvent.click(screen.getByRole("button", { name: /actionStartFlow/i }));
    expect(onStartFlow).toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
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
