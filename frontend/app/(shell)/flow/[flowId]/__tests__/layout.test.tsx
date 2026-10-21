// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/** Ruling UX-3 (M7) — the flow layout's stepper on a flow that skipped the interview. */
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import FlowLayout from "../layout";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/flow/f1/cv",
}));
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));
vi.mock("@/lib/auth/current-user", () => ({
  useCurrentUser: () => ({ user: null, isAdmin: false, signOut: vi.fn() }),
}));

function fulfilledParams(flowId: string) {
  const p = Promise.resolve({ flowId });
  return Object.assign(p, { status: "fulfilled", value: { flowId } });
}

function renderWith(interview_summary: unknown) {
  global.fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({
      flow_id: "f1",
      user_type: "new",
      current_step: "cv_generation",
      available_actions: {},
      interview_summary,
    }),
  })) as unknown as typeof fetch;
  render(
    <FlowLayout params={fulfilledParams("f1")}>
      <div />
    </FlowLayout>,
  );
}

const pill = (label: string) => screen.getByText(label).closest("span[data-state]");

describe("FlowLayout stepper (M7)", () => {
  it("Generate CV Now without an interview: '3 Interview' is skipped, not done", async () => {
    renderWith(null);
    await waitFor(() => expect(pill("flow.stepInterview")).toHaveAttribute("data-state", "skipped"));
    expect(pill("flow.stepGaps")).toHaveAttribute("data-state", "done");
    expect(pill("flow.stepCV")).toHaveAttribute("data-state", "active");
  });

  it("after a completed interview '3 Interview' is done", async () => {
    renderWith({ session_id: "s1", mode: "targeted", status: "complete", questions_asked: 5, hard_ceiling: 12 });
    await waitFor(() => expect(pill("flow.stepInterview")).toHaveAttribute("data-state", "done"));
  });
});
