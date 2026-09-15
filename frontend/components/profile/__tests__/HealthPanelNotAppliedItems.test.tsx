// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * #705 (founder UAT on the Nougat RC, 2026-09-15) — the not-retained overlay
 * must name WHICH items, per section, instead of a 3-item-capped sentence and
 * a single action that "just randomly moves to one of the affected sections".
 *
 * `groupNotAppliedItems` is the composition the overlay renders from: the
 * backend's full `not_applied_items` receipt (#705), grouped by section, each
 * group carrying its own localized label list and reason clause(s). Falls
 * back to an empty array when the backend has not sent the field (a record
 * from a backend predating #705) so the caller can keep the old single
 * summary+action rendering.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { useTranslations } from "next-intl";
import { groupNotAppliedItems, type HealthIssue } from "../HealthPanel";
import { withIntl } from "@/lib/test-utils/with-intl";

function issueWith(
  items: { section: string | null; label: string; reason: string }[],
): HealthIssue {
  return {
    id: "not_applied:rec-1",
    thread: "not_applied",
    profile_mismatch_severity: "review",
    summary: "fallback, should not be used when items are present",
    not_applied_count: items.length,
    not_applied_source: "cv_upload",
    not_applied_reasons: Array.from(new Set(items.map((i) => i.reason))),
    not_applied_labels: items.slice(0, 3).map((i) => i.label),
    not_applied_items: items,
  };
}

function twentyThreeItemsAcrossFiveSections() {
  const sections = ["work_experience", "education", "skills", "languages", "projects"];
  return Array.from({ length: 23 }, (_, n) => ({
    section: sections[n % sections.length],
    label: `${sections[n % sections.length]} entry ${n}`,
    reason: n % 2 === 0 ? "no_op_carried_entry" : "op_rejected",
  }));
}

function Probe({ items }: { items: { section: string | null; label: string; reason: string }[] }) {
  const t = useTranslations("health");
  const tProfile = useTranslations("profile");
  const groups = groupNotAppliedItems(issueWith(items), t, tProfile);
  return (
    <ul>
      {groups.map((g) => (
        <li key={g.section ?? "none"} data-testid="group">
          <span data-testid="group-heading">{g.sectionLabel}</span>
          <span data-testid="group-labels">{g.labels.join(", ")}</span>
          <span data-testid="group-reason">{g.reasonText}</span>
        </li>
      ))}
    </ul>
  );
}

describe("groupNotAppliedItems", () => {
  it("groups all 23 items into their five sections, none dropped", () => {
    const items = twentyThreeItemsAcrossFiveSections();
    render(withIntl(<Probe items={items} />, "en"));
    const groups = screen.getAllByTestId("group");
    expect(groups).toHaveLength(5);
    const totalLabels = screen
      .getAllByTestId("group-labels")
      .reduce((sum, el) => sum + el.textContent!.split(", ").length, 0);
    expect(totalLabels).toBe(23);
  });

  it("names the section with its localized label and count", () => {
    const items = [
      { section: "work_experience", label: "Acme Corp", reason: "no_op_carried_entry" },
      { section: "work_experience", label: "Beta GmbH", reason: "no_op_carried_entry" },
    ];
    render(withIntl(<Probe items={items} />, "en"));
    expect(screen.getByTestId("group-heading").textContent).toContain("Work Experience");
    expect(screen.getByTestId("group-labels").textContent).toBe("Acme Corp, Beta GmbH");
  });

  it("renders the German section label under the de locale", () => {
    const items = [
      { section: "work_experience", label: "Acme Corp", reason: "no_op_carried_entry" },
    ];
    render(withIntl(<Probe items={items} />, "de"));
    expect(screen.getByTestId("group-heading").textContent).toContain("Berufserfahrung");
  });

  it("composes the reason clause per group, reusing the existing lowercase copies", () => {
    const items = [
      { section: "skills", label: "Rust", reason: "op_rejected" },
    ];
    render(withIntl(<Probe items={items} />, "en"));
    expect(screen.getByTestId("group-reason").textContent).toContain(
      "the change came back malformed and was dropped",
    );
  });

  it("maps a professional_summary item's language slot the same way the hub sentence does", () => {
    const items = [{ section: "professional_summary", label: "en", reason: "summary_populated" }];
    render(withIntl(<Probe items={items} />, "en"));
    expect(screen.getByTestId("group-labels").textContent).toBe("your self-description (English)");
    expect(screen.getByTestId("group-labels").textContent).not.toBe("en");
  });

  it("returns an empty array when the backend has not sent the full item list", () => {
    const issue: HealthIssue = {
      id: "not_applied:legacy",
      thread: "not_applied",
      profile_mismatch_severity: "review",
      summary: "2 items did not reach your profile",
      not_applied_count: 2,
      not_applied_source: "cv_upload",
      not_applied_reasons: ["no_op_carried_entry"],
      not_applied_labels: ["A", "B"],
      // not_applied_items intentionally absent — a pre-#705 backend response.
    };
    function LegacyProbe() {
      const t = useTranslations("health");
      const tProfile = useTranslations("profile");
      return <div data-testid="count">{groupNotAppliedItems(issue, t, tProfile).length}</div>;
    }
    render(withIntl(<LegacyProbe />, "en"));
    expect(screen.getByTestId("count").textContent).toBe("0");
  });
});
