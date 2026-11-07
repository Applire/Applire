// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Hazelnut build 7 WP-V (789:258 + 789:262) — after an import with no posting
 * the in-flow import page says the profile is ready, counts the hub's open
 * questions and names the next step.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ImportReadyCard, importIsReadyForFlow, openQuestionCount } from "../ImportReadyCard";
import { withIntl } from "@/lib/test-utils/with-intl";

const SUMMARY = {
  record_id: "r1",
  timestamp: "2026-11-07T09:05:00Z",
  source: "cv_upload",
  file_name: "cv.pdf",
  added_total: 34,
  groups: [],
  matched: [
    { section: "skills", entity_id: "s1", incoming: "A", existing: "B", basis: "model" },
    { section: "skills", entity_id: "s2", incoming: "C", existing: "D", basis: "model", undone_at: "2026-11-07T09:06:00Z" },
  ],
  kept_separate: [],
  restated: [],
  not_applied_total: 0,
};
const FLOW = { flow_id: "f1", job_id: null, created_at: "2026-11-07T09:00:00Z" };
const HEALTH = {
  issues: [
    { id: "i1", thread: "duplicate_entry", profile_mismatch_severity: "review", summary: "", delivery_class: "A" },
    { id: "i2", thread: "conflict", profile_mismatch_severity: "review", summary: "", delivery_class: "A" },
    { id: "i3", thread: "not_applied", profile_mismatch_severity: "info", summary: "", delivery_class: "A" },
    { id: "i4", thread: "confirmation", profile_mismatch_severity: "info", summary: "", delivery_class: "S" },
  ],
};

function route(flow: unknown, summary: unknown, health: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const body = url.includes("/api/flow/") ? flow : url.includes("import-summary") ? summary : health;
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    }),
  );
}

afterEach(() => vi.restoreAllMocks());

describe("ImportReadyCard", () => {
  it("says the profile is ready, counts the open questions and names the next step", async () => {
    route(FLOW, SUMMARY, HEALTH);
    render(withIntl(<ImportReadyCard flowId="f1" />, "de"));
    await waitFor(() => expect(screen.getByTestId("import-ready-card")).toBeInTheDocument());
    expect(screen.getByText("Dein Profil ist angelegt")).toBeInTheDocument();
    expect(screen.getByTestId("import-ready-lead")).toHaveTextContent(
      "Wir haben 34 Angaben in dein Profil übernommen und 1 als schon vorhanden erkannt.",
    );
    await waitFor(() =>
      expect(screen.getByTestId("import-ready-questions")).toHaveTextContent("2 Fragen zu deinem Profil warten."),
    );
    expect(screen.getByTestId("import-ready-answer")).toHaveAttribute("href", "/profile#profile-health");
    expect(screen.getByTestId("import-ready-view-profile")).toHaveAttribute("href", "/profile#import-summary");
    expect(screen.getByTestId("import-ready-check-job")).toHaveAttribute("href", "/");
  });

  it("shows no question line when the hub asks nothing", async () => {
    route(FLOW, SUMMARY, { issues: [] });
    render(withIntl(<ImportReadyCard flowId="f1" />, "en"));
    await waitFor(() => expect(screen.getByText("Your profile is ready")).toBeInTheDocument());
    expect(screen.queryByTestId("import-ready-questions")).toBeNull();
  });

  it("stays away when the flow has a job", async () => {
    route({ ...FLOW, job_id: "j1" }, SUMMARY, HEALTH);
    const { container } = render(withIntl(<ImportReadyCard flowId="f1" />, "de"));
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2));
    expect(container).toBeEmptyDOMElement();
  });
});

describe("importIsReadyForFlow", () => {
  it("needs a job-less flow and an import made after the flow began", () => {
    expect(importIsReadyForFlow(FLOW, SUMMARY)).toBe(true);
    expect(importIsReadyForFlow(FLOW, { ...SUMMARY, timestamp: "2026-11-07T08:59:59Z" })).toBe(false);
    expect(importIsReadyForFlow({ ...FLOW, job_id: "j1" }, SUMMARY)).toBe(false);
    expect(importIsReadyForFlow(FLOW, { ...SUMMARY, record_id: null })).toBe(false);
    expect(importIsReadyForFlow(null, SUMMARY)).toBe(false);
  });
});

describe("openQuestionCount", () => {
  it("counts the rendered hub threads that ask the user something", () => {
    expect(openQuestionCount(HEALTH as never)).toBe(2);
    expect(openQuestionCount(null)).toBe(0);
  });
});
