// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * #838 line 23 (WP-Q4 e) — the `accuracy` thread was the last Health thread
 * rendering the backend's English `summary` verbatim in a German UI. The reader
 * now composes it from the pieces (`describeAccuracy`); the English summary is
 * only the fallback for a backend that predates them. Real catalogs.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { useTranslations } from "next-intl";
import { describeAccuracy, type HealthIssue } from "../HealthPanel";
import { withIntl } from "@/lib/test-utils/with-intl";

const ENGLISH_LOST = "Merge from cv_upload did not retain 12 extracted item(s) (education, skills)";
const ENGLISH_LOW = "Low-confidence merge from cv_upload (40%)";

function Probe({ issue }: { issue: HealthIssue }) {
  const t = useTranslations("health");
  const tProfile = useTranslations("profile");
  return <div data-testid="out">{describeAccuracy(issue, t, tProfile)}</div>;
}

const base = { id: "accuracy:r1", thread: "accuracy", profile_mismatch_severity: "critical" } as const;

describe("describeAccuracy (#838:23)", () => {
  it("composes the lost-items sentence in German with section labels, never the English summary", () => {
    const issue = { ...base, summary: ENGLISH_LOST, field_ref: "education, skills",
      accuracy_kind: "lost", accuracy_count: 12, accuracy_source: "cv_upload" } as HealthIssue;
    render(withIntl(<Probe issue={issue} />, "de"));
    const text = screen.getByTestId("out").textContent ?? "";
    expect(text).toContain("12 Angaben aus");
    expect(text).not.toContain("did not retain");
    expect(text).not.toContain("cv_upload");
    expect(text).not.toMatch(/\beducation\b|\bskills\b/);
  });

  it("composes the low-confidence sentence in German", () => {
    const issue = { ...base, profile_mismatch_severity: "review", summary: ENGLISH_LOW,
      accuracy_kind: "low_confidence", accuracy_percent: 40, accuracy_source: "cv_upload" } as HealthIssue;
    render(withIntl(<Probe issue={issue} />, "de"));
    const text = screen.getByTestId("out").textContent ?? "";
    expect(text).toContain("40 % Sicherheit");
    expect(text).not.toContain("Low-confidence");
  });

  it("falls back to the server summary when the pieces are absent", () => {
    const issue = { ...base, summary: ENGLISH_LOW } as HealthIssue;
    render(withIntl(<Probe issue={issue} />, "en"));
    expect(screen.getByTestId("out").textContent).toBe(ENGLISH_LOW);
  });
});
