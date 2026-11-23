// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { act, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { PlanCard } from "../PlanCard";
import { announceAllowanceChanged } from "../events";
import { allowance, hunter, mockApi, noPlan, renderWithUser } from "./fixtures";

let api: ReturnType<typeof mockApi>;
afterEach(() => {
  cleanup();
  api?.restore();
});
const puts = () => api.calls.filter((c) => c.method === "PUT");

describe("PlanCard", () => {
  it("shows the plan, both meters and the A2 strings (DE)", async () => {
    api = mockApi({ current: allowance() });
    renderWithUser(<PlanCard />, "de");
    const card = await screen.findByTestId("plan-card");
    expect(card.textContent).toContain("Dein Abo");
    expect(screen.getByTestId("plan-line").textContent).toBe("Jobseeker · 5 Bewerbungen im Monat");
    expect(card.textContent).toContain("Testabo – es wird nichts abgebucht");
    expect(card.textContent).toContain("Noch 2 von 5 Bewerbungen");
    expect(card.textContent).toContain("Noch 2 von 3 Lebenslauf-Importen");
    expect(card.textContent).toContain("erneuert am 1. Dezember");
    expect(screen.getByTestId("plan-upgrade").textContent).toBe("Zu Jobhunter wechseln");
    expect(screen.getByTestId("plan-buy").textContent).toBe("Bewerbungen kaufen");
    expect((screen.getByTestId("plan-apps-bar").firstChild as HTMLElement).style.width).toBe("60%");
  });

  it("an upgrade goes through the summary, then one PUT", async () => {
    api = mockApi({ current: allowance(), putResult: { change: "upgraded", allowance: hunter() } });
    renderWithUser(<PlanCard />);
    await userEvent.click(await screen.findByTestId("plan-upgrade"));
    expect(puts()).toHaveLength(0);
    expect((await screen.findByTestId("plan-summary-dialog")).textContent).toContain("Jobhunter · €24.90 / month");
    await userEvent.click(screen.getByTestId("plan-summary-confirm"));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toEqual({ plan_id: "jobhunter" });
  });

  it("A7: shows the upgrade notice after an upgrade", async () => {
    api = mockApi({ current: hunter({ applications: { limit: 20, used: 5, remaining: 15, purchased_remaining: 5, has_plan: true } }) });
    renderWithUser(<PlanCard />);
    await screen.findByTestId("plan-card");
    expect(screen.queryByTestId("plan-upgraded-notice")).toBeNull();
    act(() => announceAllowanceChanged("upgraded"));
    expect((await screen.findByTestId("plan-upgraded-notice")).textContent).toBe("You are now a Jobhunter. You have 15 applications left this month.");
    expect(screen.getByTestId("plan-purchased").textContent).toContain("+ 5 purchased applications");
  });

  it("the downgrade is ONE click and one PUT, no summary", async () => {
    api = mockApi({ current: hunter() });
    renderWithUser(<PlanCard />);
    const btn = await screen.findByTestId("plan-downgrade");
    expect(btn.textContent).toBe("Switch to Jobseeker from December");
    await userEvent.click(btn);
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toEqual({ plan_id: "jobseeker" });
    expect(screen.queryByTestId("plan-summary-dialog")).toBeNull();
  });

  it("a pending downgrade shows the line and 'stay' PUTs the current plan", async () => {
    api = mockApi({ current: hunter({ pending: { plan_id: "jobseeker", label: "Jobseeker", applications: 5, from: "2026-12-01" } }) });
    renderWithUser(<PlanCard />);
    expect((await screen.findByTestId("plan-pending")).textContent).toBe("From 1 December: Jobseeker · 5 applications a month.");
    expect(screen.queryByTestId("plan-downgrade")).toBeNull();
    await userEvent.click(screen.getByTestId("plan-stay"));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toEqual({ plan_id: "jobhunter" });
  });

  it("without a plan the card shows the tiles inline", async () => {
    api = mockApi({ current: noPlan() });
    renderWithUser(<PlanCard />);
    expect(await screen.findByTestId("plan-tiles")).toBeTruthy();
    expect(screen.queryByTestId("plan-buy")).toBeNull();
    await userEvent.click(screen.getByTestId("plan-choose-jobseeker"));
    expect(await screen.findByTestId("plan-summary-dialog")).toBeTruthy();
  });

  it("a purchase POSTs with an idempotency key and shows 0,00 €", async () => {
    api = mockApi({ current: allowance() });
    renderWithUser(<PlanCard />, "de");
    await userEvent.click(await screen.findByTestId("plan-buy"));
    expect((await screen.findByTestId("buy-dialog")).textContent).toContain("9,90 €");
    await userEvent.click(screen.getByTestId("buy-pack-pack10"));
    await userEvent.click(screen.getByTestId("buy-next"));
    const sum = await screen.findByTestId("buy-summary-dialog");
    expect(sum.textContent).toContain("10 Bewerbungen");
    expect(sum.textContent).toContain("17,90 €");
    expect(screen.getByTestId("buy-due").textContent).toBe("0,00 €");
    expect(screen.getByTestId("buy-confirm").textContent).toBe("Testkauf abschließen");
    await userEvent.click(screen.getByTestId("buy-confirm"));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    const post = api.calls.find((c) => c.method === "POST")!;
    expect(post.url).toBe("/api/me/purchases");
    expect(post.body).toMatchObject({ pack_id: "pack10" });
    expect(typeof (post.body as { idempotency_key: string }).idempotency_key).toBe("string");
    expect((post.body as { idempotency_key: string }).idempotency_key.length).toBeGreaterThan(8);
  });

  it("never says 'Jetzt kaufen' or 'Zahlungspflichtig bestellen'", async () => {
    api = mockApi({ current: allowance() });
    renderWithUser(<PlanCard />, "de");
    await userEvent.click(await screen.findByTestId("plan-buy"));
    await screen.findByTestId("buy-dialog");
    expect(document.body.textContent).not.toMatch(/Jetzt kaufen|Zahlungspflichtig/);
  });
});
