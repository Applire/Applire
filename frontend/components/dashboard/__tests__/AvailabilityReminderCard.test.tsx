// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { withIntl } from "@/lib/test-utils/with-intl";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import { AvailabilityReminderCard } from "../AvailabilityReminderCard";

type Call = { url: string; method: string };
let calls: Call[] = [];

function stubProfile(body: unknown, ok = true) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method ?? "GET" });
      if (String(url).endsWith("/api/profile")) {
        return { ok, json: async () => body } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
}

describe("AvailabilityReminderCard (ruling V-4)", () => {
  beforeEach(() => {
    calls = [];
    push.mockClear();
    window.localStorage.clear();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("missing → asks when the user could start, leads to the My Profile edit", async () => {
    stubProfile({ availability_reminder: "missing", profile: { availability: null } });
    render(withIntl(<AvailabilityReminderCard />, "de"));
    const card = await screen.findByTestId("availability-reminder");
    expect(card.textContent).toContain("Ab wann könntest du anfangen?");
    expect(card).toHaveAccessibleName("Hinweis zu deiner Kündigungsfrist");
    fireEvent.click(screen.getByTestId("availability-reminder-action"));
    expect(push).toHaveBeenCalledWith("/profile#availability");
  });

  it("stale → names the stored value and its date in the UI locale", async () => {
    stubProfile({
      availability_reminder: "stale",
      profile: { availability: { text: "3 Monate zum Quartalsende", recorded_at: "2026-07-12T09:00:00Z" } },
    });
    render(withIntl(<AvailabilityReminderCard />, "de"));
    const card = await screen.findByTestId("availability-reminder");
    expect(card.textContent).toContain("Stimmt deine Kündigungsfrist noch?");
    expect(card.textContent).toContain("12. Juli 2026");
    expect(card.textContent).toContain("„3 Monate zum Quartalsende“");
    expect(screen.getByTestId("availability-reminder-dismiss").textContent).toBe("Stimmt noch");
  });

  it("no reminder → renders nothing", async () => {
    stubProfile({ availability_reminder: null, profile: {} });
    const { container } = render(withIntl(<AvailabilityReminderCard />));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(container.innerHTML).toBe("");
  });

  it("an unreadable profile renders nothing (never a broken dashboard)", async () => {
    stubProfile({}, false);
    const { container } = render(withIntl(<AvailabilityReminderCard />));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(container.innerHTML).toBe("");
  });

  it("the close is stored SERVER-side (POST dismiss), never in browser storage", async () => {
    stubProfile({ availability_reminder: "missing", profile: {} });
    render(withIntl(<AvailabilityReminderCard />));
    fireEvent.click(await screen.findByTestId("availability-reminder-dismiss"));
    await waitFor(() => expect(screen.queryByTestId("availability-reminder")).toBeNull());
    expect(calls).toContainEqual({ url: "/api/profile/availability/reminder/dismiss", method: "POST" });
    expect(window.localStorage.length).toBe(0);
  });
});
