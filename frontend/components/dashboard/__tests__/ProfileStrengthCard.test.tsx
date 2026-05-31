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
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProfileStrengthCard } from "../ProfileStrengthCard";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

// ---------------------------------------------------------------------------
// Mock next-intl — ProfileStrengthCard uses useTranslations()
// ---------------------------------------------------------------------------
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

function mockFetch(existsData: object, profileData: object) {
  let callCount = 0;
  global.fetch = vi.fn().mockImplementation(() => {
    callCount++;
    const data = callCount === 1 ? existsData : profileData;
    return Promise.resolve({ ok: true, json: async () => data });
  });
}

describe("ProfileStrengthCard", () => {
  beforeEach(() => {
    mockPush.mockReset();
    vi.restoreAllMocks();
  });

  it("shows loading skeleton before fetch resolves", () => {
    global.fetch = vi.fn().mockImplementation(() => new Promise(() => {}));
    render(<ProfileStrengthCard />);
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
  });

  it("renders score after fetch resolves", async () => {
    mockFetch(
      { exists: true, completeness_score: 0.72 },
      { profile: { work_experience: [{}], skills: [{}], education: [], personal_info: {} } }
    );
    render(<ProfileStrengthCard />);
    await waitFor(() => expect(screen.getByText("72")).toBeInTheDocument());
  });

  it("shows Berufserfahrung as done when work_experience present", async () => {
    mockFetch(
      { completeness_score: 0.5 },
      { profile: { work_experience: [{}], skills: [], education: [], personal_info: {} } }
    );
    render(<ProfileStrengthCard />);
    await waitFor(() => expect(screen.getByText("profileCheckWorkExp")).toBeInTheDocument());
    const item = screen.getByText("profileCheckWorkExp");
    expect(item.className).toContain("text-white/75");
  });

  it("shows Fähigkeiten as pending when skills empty", async () => {
    mockFetch(
      { completeness_score: 0.2 },
      { profile: { work_experience: [], skills: [], education: [], personal_info: {} } }
    );
    render(<ProfileStrengthCard />);
    await waitFor(() => expect(screen.getByText("profileCheckSkills")).toBeInTheDocument());
    const item = screen.getByText("profileCheckSkills");
    expect(item.className).toContain("text-white/40");
  });

  it("shows Zusammenfassung as done when summary present", async () => {
    mockFetch(
      { completeness_score: 0.8 },
      { profile: { work_experience: [], skills: [], education: [], personal_info: { summary: "I am great." } } }
    );
    render(<ProfileStrengthCard />);
    await waitFor(() => expect(screen.getByText("profileCheckSummary")).toBeInTheDocument());
    expect(screen.getByText("profileCheckSummary").className).toContain("text-white/75");
  });

  it("Complete Profile button navigates to /profile", async () => {
    mockFetch({ completeness_score: 0.3 }, { profile: null });
    render(<ProfileStrengthCard />);
    await waitFor(() => screen.getByText("profileCompleteButton"));
    await userEvent.click(screen.getByText("profileCompleteButton"));
    expect(mockPush).toHaveBeenCalledWith("/profile");
  });

  it("stays in skeleton state when fetch fails", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network error"));
    render(<ProfileStrengthCard />);
    await waitFor(() => {}, { timeout: 200 });
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
  });
});
