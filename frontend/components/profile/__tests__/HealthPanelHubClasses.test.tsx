// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Hazelnut build 5 (UAT-3d, U-9; ADR-099 cl. 2/11) — the profile health hub per
 * delivery class:
 *  - class S is never rendered (e.g. `accuracy`, "Datenabgleich"); an
 *    UNCLASSIFIED issue renders exactly as before;
 *  - a keyed `not_applied` issue offers "Übernehmen" per item, or "go to
 *    section" for an item the reconcile door cannot take;
 *  - a budget figure's unit is accepted in place;
 *  - a duplicate-station pair asks ONE question: "Ja" → merge preview,
 *    "Nein" → kept separate.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HealthPanel, type HealthIssue, type ProfileHealth } from "../HealthPanel";
import { withIntl } from "@/lib/test-utils/with-intl";

const health = (issues: HealthIssue[]): ProfileHealth => ({
  issues,
  completeness: { score: 0.9, gaps: [], field_gaps: [] },
});

const accuracy: HealthIssue = {
  id: "accuracy:1",
  thread: "accuracy",
  profile_mismatch_severity: "review",
  summary: "Datenabgleich: the summary names 12 years",
};

const notApplied = (adoptable: boolean[]): HealthIssue => ({
  id: "not_applied:rec-1",
  thread: "not_applied",
  profile_mismatch_severity: "review",
  summary: "fallback",
  not_applied_count: adoptable.length,
  not_applied_source: "cv_upload",
  not_applied_reasons: ["no_op_carried_entry"],
  not_applied_items: adoptable.map((a, n) => ({
    section: "skills",
    label: `Skill ${n}`,
    reason: "no_op_carried_entry",
    item_key: `k${n}`,
    adoptable: a,
  })),
});

const unit: HealthIssue = {
  id: "unit:w1",
  thread: "unit",
  profile_mismatch_severity: "review",
  summary: "budget without unit",
  delivery_class: "U",
  budget_value: "2,4 Mio.",
  entry_id: "w1",
  source_record_ref: "Quality Lead @ Nordwerk Logistik GmbH",
};

const duplicate: HealthIssue = {
  id: "duplicate_entry:w1:w2",
  thread: "duplicate_entry",
  profile_mismatch_severity: "review",
  summary: "same station?",
  delivery_class: "U",
  section: "work_experience",
  entry_ids: ["w1", "w2"],
  entity_label: "Qualitätsleiter @ Nordwerk Logistik GmbH",
  other_entity_label: "Quality Lead @ Nordwerk Logistics",
};

describe("HealthPanel — hub per delivery class", () => {
  it("never renders a class-S issue; an unclassified one renders as before", () => {
    const { unmount } = render(
      withIntl(<HealthPanel health={health([{ ...accuracy, delivery_class: "S" }])} onResolve={vi.fn()} />, "de"),
    );
    expect(screen.queryByTestId("health-issue")).toBeNull();
    expect(screen.queryByText(/Datenabgleich/)).toBeNull();
    unmount();
    render(withIntl(<HealthPanel health={health([accuracy])} onResolve={vi.fn()} />, "de"));
    expect(screen.getAllByTestId("health-issue")).toHaveLength(1);
  });

  it("counts only the rendered issues in the summary line", () => {
    render(
      withIntl(
        <HealthPanel
          health={health([{ ...accuracy, delivery_class: "S" }, { ...accuracy, id: "a2", delivery_class: "A" }])}
          onResolve={vi.fn()}
        />,
        "en",
      ),
    );
    expect(screen.getAllByTestId("health-issue")).toHaveLength(1);
  });

  it("UAT-3d: 'Übernehmen' per not-applied item sends that item's key", async () => {
    const onAdoptItem = vi.fn().mockResolvedValue(true);
    render(
      withIntl(
        <HealthPanel health={health([notApplied([true, true])])} onResolve={vi.fn()} actions={{ onAdoptItem }} />,
        "de",
      ),
    );
    const rows = screen.getAllByTestId("health-not-applied-item");
    expect(rows).toHaveLength(2);
    expect(screen.queryByTestId("health-resolve")).toBeNull();
    fireEvent.click(screen.getAllByTestId("health-adopt-item")[1]);
    await waitFor(() => expect(onAdoptItem).toHaveBeenCalledWith(expect.objectContaining({ id: "not_applied:rec-1" }), "k1"));
    expect(screen.getAllByTestId("health-adopt-item")[0]).toHaveTextContent("Übernehmen");
  });

  it("an item the door cannot take offers 'go to section' instead", () => {
    const onGoToSection = vi.fn();
    render(
      withIntl(
        <HealthPanel
          health={health([notApplied([true, false])])}
          onResolve={vi.fn()}
          actions={{ onAdoptItem: vi.fn(), onGoToSection }}
        />,
        "en",
      ),
    );
    expect(screen.getAllByTestId("health-adopt-item")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("health-not-applied-go-to-section"));
    expect(onGoToSection).toHaveBeenCalledWith("skills", ["Skill 1"]);
  });

  it("a not-applied issue without item keys keeps the #705 route", () => {
    const legacy = notApplied([true]);
    legacy.not_applied_items = legacy.not_applied_items!.map(({ section, label, reason }) => ({ section, label, reason }));
    render(withIntl(<HealthPanel health={health([legacy])} onResolve={vi.fn()} actions={{ onAdoptItem: vi.fn() }} />, "en"));
    expect(screen.queryByTestId("health-adopt-item")).toBeNull();
    expect(screen.getByTestId("health-resolve")).toBeInTheDocument();
  });

  it("UAT-3d: the unit is chosen and saved in place", async () => {
    const onSaveUnit = vi.fn().mockResolvedValue(true);
    render(withIntl(<HealthPanel health={health([unit])} onResolve={vi.fn()} actions={{ onSaveUnit }} />, "de"));
    expect(screen.getByTestId("health-issue-summary")).toHaveTextContent("„2,4 Mio.“");
    expect(screen.queryByTestId("health-resolve")).toBeNull();
    fireEvent.change(screen.getByTestId("health-unit-select"), { target: { value: "CHF" } });
    fireEvent.click(screen.getByTestId("health-unit-save"));
    await waitFor(() => expect(onSaveUnit).toHaveBeenCalledWith(expect.objectContaining({ id: "unit:w1" }), "CHF"));
  });

  it("a failed unit save says so and keeps the control", async () => {
    const onSaveUnit = vi.fn().mockResolvedValue(false);
    render(withIntl(<HealthPanel health={health([unit])} onResolve={vi.fn()} actions={{ onSaveUnit }} />, "en"));
    fireEvent.click(screen.getByTestId("health-unit-save"));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByTestId("health-unit-select")).toBeInTheDocument();
  });

  it("U-9: a duplicate-station pair asks once; 'Ja' opens the merge, 'Nein' keeps them separate", async () => {
    const onMergeDuplicate = vi.fn();
    const onKeepSeparate = vi.fn().mockResolvedValue(true);
    render(
      withIntl(
        <HealthPanel health={health([duplicate])} onResolve={vi.fn()} actions={{ onMergeDuplicate, onKeepSeparate }} />,
        "de",
      ),
    );
    expect(screen.getByTestId("health-issue-duplicate-entry")).toHaveTextContent(
      "Sind Qualitätsleiter @ Nordwerk Logistik GmbH und Quality Lead @ Nordwerk Logistics dieselbe Station?",
    );
    expect(screen.queryByTestId("health-resolve")).toBeNull();
    fireEvent.click(screen.getByTestId("health-duplicate-merge"));
    expect(onMergeDuplicate).toHaveBeenCalledWith(expect.objectContaining({ id: duplicate.id }));
    fireEvent.click(screen.getByTestId("health-duplicate-separate"));
    await waitFor(() => expect(onKeepSeparate).toHaveBeenCalledWith(expect.objectContaining({ id: duplicate.id })));
  });
});
