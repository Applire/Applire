// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { HealthPanel, type HealthIssue, type ProfileHealth } from "../HealthPanel";
import { withIntl } from "@/lib/test-utils/with-intl";

const SERVER_SUMMARY = "SERVER-FALLBACK-SUMMARY should never be rendered";

const SPAN_ISSUE: HealthIssue = {
  id: "span_provenance:SAP CO",
  thread: "span_provenance",
  profile_mismatch_severity: "review",
  summary: SERVER_SUMMARY,
  field_ref: "skills.years_experience",
  source_record_ref: "SAP CO",
  skill_id: "s-co",
  skill_name: "SAP CO",
  span_years: 8,
  recomputed_years: 6,
};

const healthOf = (issues: HealthIssue[]): ProfileHealth => ({
  issues,
  completeness: { score: 0.8, gaps: [], field_gaps: [] },
});

describe("HealthPanel — span_provenance (#764)", () => {
  it("composes the sentence in English and never renders the server summary", () => {
    render(withIntl(<HealthPanel health={healthOf([SPAN_ISSUE])} onResolve={vi.fn()} onRecomputeSpan={vi.fn()} />, "en"));
    const sentence = screen.getByTestId("health-issue-span-provenance");
    expect(sentence.textContent).toBe(
      "SAP CO: 8 years is marked as something you said, but nothing you wrote or told us states it. Your role dates give 6 years.",
    );
    expect(screen.queryByText(/SERVER-FALLBACK/)).toBeNull();
    expect(screen.getByText("Skill duration")).toBeTruthy();
    expect(screen.getByTestId("health-recompute-span").textContent).toBe("Recalculate from role dates");
  });

  it("composes the sentence in German and never renders the server summary", () => {
    render(withIntl(<HealthPanel health={healthOf([SPAN_ISSUE])} onResolve={vi.fn()} onRecomputeSpan={vi.fn()} />, "de"));
    const sentence = screen.getByTestId("health-issue-span-provenance");
    expect(sentence.textContent).toContain("SAP CO: 8 Jahre sind als deine eigene Angabe vermerkt");
    expect(sentence.textContent).toContain("ergeben sich 6 Jahre.");
    expect(screen.queryByText(/SERVER-FALLBACK/)).toBeNull();
    expect(screen.getByText("Dauer einer Fähigkeit")).toBeTruthy();
    expect(screen.getByTestId("health-recompute-span").textContent).toBe("Aus den Stationen neu berechnen");
  });

  it("calls onRecomputeSpan once with the issue and is disabled while pending", async () => {
    let resolve!: () => void;
    const onRecomputeSpan = vi.fn(() => new Promise<void>((r) => { resolve = r; }));
    const onResolve = vi.fn();
    render(withIntl(<HealthPanel health={healthOf([SPAN_ISSUE])} onResolve={onResolve} onRecomputeSpan={onRecomputeSpan} />));
    const button = screen.getByTestId("health-recompute-span") as HTMLButtonElement;
    fireEvent.click(button);
    expect(onRecomputeSpan).toHaveBeenCalledTimes(1);
    expect(onRecomputeSpan).toHaveBeenCalledWith(SPAN_ISSUE);
    expect(button.disabled).toBe(true);
    expect(button.textContent).toBe("Recalculating…");
    fireEvent.click(button);
    expect(onRecomputeSpan).toHaveBeenCalledTimes(1);
    await act(async () => { resolve(); });
    expect(button.disabled).toBe(false);
    expect(button.textContent).toBe("Recalculate from role dates");
    expect(onResolve).not.toHaveBeenCalled();
    expect(screen.queryByTestId("health-resolve")).toBeNull();
  });

  it("falls back to the Resolve button when no onRecomputeSpan is passed", () => {
    const onResolve = vi.fn();
    render(withIntl(<HealthPanel health={healthOf([SPAN_ISSUE])} onResolve={onResolve} />));
    expect(screen.queryByTestId("health-recompute-span")).toBeNull();
    fireEvent.click(screen.getByTestId("health-resolve"));
    expect(onResolve).toHaveBeenCalledWith(SPAN_ISSUE);
  });

  it("leaves other threads unchanged: a conflict issue still shows Resolve even with the handler", () => {
    const conflict: HealthIssue = {
      id: "conflict:abc",
      thread: "conflict",
      profile_mismatch_severity: "review",
      summary: "x",
      entity_label: "Senior Developer @ Acme Corp",
      section: "work_experience",
      field: "start_date",
      existing_value_display: "2020-01",
      incoming_value_display: "2019-06",
      incoming_source: "cv_upload",
    };
    const unit: HealthIssue = {
      id: "unit:1",
      thread: "unit",
      profile_mismatch_severity: "review",
      summary: "x",
      source_record_ref: "Acme budget",
    };
    render(withIntl(<HealthPanel health={healthOf([conflict, unit])} onResolve={vi.fn()} onRecomputeSpan={vi.fn()} />));
    expect(screen.getAllByTestId("health-resolve")).toHaveLength(2);
    expect(screen.queryByTestId("health-recompute-span")).toBeNull();
    expect(screen.queryByTestId("health-issue-span-provenance")).toBeNull();
  });
});
