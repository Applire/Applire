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

import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import {
  scoreColor,
  formatScore,
  scoreBarClass,
  SCORE_GREEN_THRESHOLD,
  SCORE_AMBER_THRESHOLD,
} from "@/lib/match-utils";
import { JobCard, JobMatchResult } from "../JobCard";

// ---------------------------------------------------------------------------
// Mock next/navigation — JobCard uses useRouter().push()
// ---------------------------------------------------------------------------
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

// ---------------------------------------------------------------------------
// Mock next-intl — JobCard uses useTranslations()
// ---------------------------------------------------------------------------
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(",")}` : key,
}));

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function makeJob(overrides: Partial<JobMatchResult> = {}): JobMatchResult {
  return {
    job_id: "job-123",
    role_title: "Software Engineer",
    company_name: "Acme GmbH",
    berufsbild_code: "43104",
    berufsbild_label: "Softwareentwicklung",
    llm_match_score: 0.8,
    embedding_similarity: 0.75,
    combined_score: 0.78,
    gap_analysis_id: "gap-abc",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// match-utils unit tests
// ---------------------------------------------------------------------------

describe("match-utils", () => {
  describe("scoreColor", () => {
    it("returns 'success' at green threshold", () => {
      expect(scoreColor(SCORE_GREEN_THRESHOLD)).toBe("success");
    });
    it("returns 'success' above green threshold", () => {
      expect(scoreColor(1.0)).toBe("success");
    });
    it("returns 'warning' at amber threshold", () => {
      expect(scoreColor(SCORE_AMBER_THRESHOLD)).toBe("warning");
    });
    it("returns 'warning' between amber and green", () => {
      expect(scoreColor(0.55)).toBe("warning");
    });
    it("returns 'critical' below amber threshold", () => {
      expect(scoreColor(0.0)).toBe("critical");
    });
    it("returns 'critical' just below amber threshold", () => {
      expect(scoreColor(SCORE_AMBER_THRESHOLD - 0.01)).toBe("critical");
    });
  });

  describe("formatScore", () => {
    it("formats 0 as '0%'", () => {
      expect(formatScore(0)).toBe("0%");
    });
    it("formats 1 as '100%'", () => {
      expect(formatScore(1)).toBe("100%");
    });
    it("formats 0.5 as '50%'", () => {
      expect(formatScore(0.5)).toBe("50%");
    });
    it("rounds to nearest integer", () => {
      expect(formatScore(0.725)).toBe("73%");
    });
    it("formats 0.78 as '78%'", () => {
      expect(formatScore(0.78)).toBe("78%");
    });
  });

  describe("scoreBarClass", () => {
    it("returns bg-success for high scores", () => {
      expect(scoreBarClass(0.9)).toBe("bg-success");
    });
    it("returns bg-warning for mid scores", () => {
      expect(scoreBarClass(0.5)).toBe("bg-warning");
    });
    it("returns bg-critical for low scores", () => {
      expect(scoreBarClass(0.2)).toBe("bg-critical");
    });
    it("returns bg-success at exact green threshold", () => {
      expect(scoreBarClass(SCORE_GREEN_THRESHOLD)).toBe("bg-success");
    });
    it("returns bg-warning at exact amber threshold", () => {
      expect(scoreBarClass(SCORE_AMBER_THRESHOLD)).toBe("bg-warning");
    });
  });
});

// ---------------------------------------------------------------------------
// JobCard component tests
// ---------------------------------------------------------------------------

describe("JobCard", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders role title and company name", () => {
    render(<JobCard job={makeJob()} />);
    expect(screen.getByTestId("job-card-title").textContent).toBe("Software Engineer");
    expect(screen.getByTestId("job-card-company").textContent).toBe("Acme GmbH");
  });

  it("omits company element when company_name is null", () => {
    render(<JobCard job={makeJob({ company_name: null })} />);
    expect(screen.queryByTestId("job-card-company")).toBeNull();
  });

  it("displays formatted score", () => {
    render(<JobCard job={makeJob({ combined_score: 0.78 })} />);
    expect(screen.getByTestId("job-card-score").textContent).toBe("78%");
  });

  it("score bar fill has correct width at 0%", () => {
    render(<JobCard job={makeJob({ combined_score: 0 })} />);
    const fill = screen.getByTestId("score-bar-fill") as HTMLElement;
    expect(fill.style.width).toBe("0%");
  });

  it("score bar fill has correct width at 50%", () => {
    render(<JobCard job={makeJob({ combined_score: 0.5 })} />);
    const fill = screen.getByTestId("score-bar-fill") as HTMLElement;
    expect(fill.style.width).toBe("50%");
  });

  it("score bar fill has correct width at 100%", () => {
    render(<JobCard job={makeJob({ combined_score: 1.0 })} />);
    const fill = screen.getByTestId("score-bar-fill") as HTMLElement;
    expect(fill.style.width).toBe("100%");
  });

  it("score bar fill uses bg-success class for high score", () => {
    render(<JobCard job={makeJob({ combined_score: 0.9 })} />);
    const fill = screen.getByTestId("score-bar-fill");
    expect(fill.className).toContain("bg-success");
  });

  it("score bar fill uses bg-warning class for mid score", () => {
    render(<JobCard job={makeJob({ combined_score: 0.5 })} />);
    const fill = screen.getByTestId("score-bar-fill");
    expect(fill.className).toContain("bg-warning");
  });

  it("score bar fill uses bg-critical class for low score", () => {
    render(<JobCard job={makeJob({ combined_score: 0.2 })} />);
    const fill = screen.getByTestId("score-bar-fill");
    expect(fill.className).toContain("bg-critical");
  });

  it("renders berufsbild badge when berufsbild_label is set", () => {
    render(<JobCard job={makeJob({ berufsbild_label: "Softwareentwicklung" })} />);
    expect(screen.getByTestId("berufsbild-badge").textContent).toBe("Softwareentwicklung");
  });

  it("omits berufsbild badge when berufsbild_label is null", () => {
    render(<JobCard job={makeJob({ berufsbild_label: null })} />);
    expect(screen.queryByTestId("berufsbild-badge")).toBeNull();
  });

  it("renders seniority badge 'Senior' for senior roles", () => {
    render(<JobCard job={makeJob({ role_title: "Senior Software Engineer" })} />);
    expect(screen.getByTestId("seniority-badge").textContent).toBe("Senior");
  });

  it("renders seniority badge 'Junior' for junior roles", () => {
    render(<JobCard job={makeJob({ role_title: "Junior Developer" })} />);
    expect(screen.getByTestId("seniority-badge").textContent).toBe("Junior");
  });

  it("renders seniority badge 'Mid' for generic roles", () => {
    render(<JobCard job={makeJob({ role_title: "Software Engineer" })} />);
    expect(screen.getByTestId("seniority-badge").textContent).toBe("Mid");
  });

  it("renders strength pills (max 3)", () => {
    render(
      <JobCard
        job={makeJob()}
        strengths={["Python", "FastAPI", "Docker", "Kubernetes"]}
      />
    );
    const pills = screen.getAllByTestId("strength-pill");
    expect(pills).toHaveLength(3);
    // Mock returns "strengthPrefix:<label>" — verify the label value is present
    expect(pills[0].textContent).toContain("Python");
  });

  it("renders gap pills (max 3)", () => {
    render(
      <JobCard
        job={makeJob()}
        gaps={["Rust", "C++", "Assembly", "VHDL"]}
      />
    );
    const pills = screen.getAllByTestId("gap-pill");
    expect(pills).toHaveLength(3);
    // Mock returns "gapPrefix:<label>" — verify the label value is present
    expect(pills[0].textContent).toContain("Rust");
  });

  it("renders no pills when strengths and gaps are empty", () => {
    render(<JobCard job={makeJob()} />);
    expect(screen.queryAllByTestId("strength-pill")).toHaveLength(0);
    expect(screen.queryAllByTestId("gap-pill")).toHaveLength(0);
  });

  it("'Run gap analysis' button calls router.push with job_id", () => {
    render(<JobCard job={makeJob({ job_id: "job-456" })} />);
    fireEvent.click(screen.getByTestId("run-gap-analysis-btn"));
    expect(mockPush).toHaveBeenCalledWith("/?job_id=job-456");
  });
});
