// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { act, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { AllowanceStartLine } from "../AllowanceStartLine";
import { PlanCard } from "../PlanCard";
import { ALLOWANCE_REFUSED_EVENT, offerPlanChoice } from "../events";
import { TestPlanNotices, resetPlanOfferForTests } from "../TestPlanNotices";
import { allowance, community, hunter, mockApi, noPlan, renderWithUser, zero } from "./fixtures";

let api: ReturnType<typeof mockApi>;
afterEach(() => {
  cleanup();
  api?.restore();
  resetPlanOfferForTests();
});

const refuse = (detail: object) => act(() => void window.dispatchEvent(new CustomEvent(ALLOWANCE_REFUSED_EVENT, { detail })));

describe("the Community seam (purchasing_available false)", () => {
  it("renders no plan card, no choice dialog and no buy trigger", async () => {
    api = mockApi({ current: community() });
    renderWithUser(
      <>
        <PlanCard />
        <TestPlanNotices />
      </>,
    );
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    offerPlanChoice();
    refuse({ code: "plan_required", action: null, renews_on: null });
    await act(async () => {});
    expect(screen.queryByTestId("plan-card")).toBeNull();
    expect(screen.queryByTestId("plan-buy")).toBeNull();
    expect(screen.queryByTestId("choose-plan-dialog")).toBeNull();
  });

  it("renders nothing at all when the allowance route is missing (old backend)", async () => {
    api = mockApi({ current: null });
    renderWithUser(
      <>
        <PlanCard />
        <AllowanceStartLine />
        <TestPlanNotices />
      </>,
    );
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    expect(screen.queryByTestId("plan-card")).toBeNull();
    expect(screen.queryByTestId("allowance-start-line")).toBeNull();
  });
});

describe("A4 zero dialog", () => {
  it("smaller plan: renew line, keep-working line, upgrade sentence, three buttons", async () => {
    api = mockApi({ current: zero() });
    renderWithUser(<TestPlanNotices />);
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    refuse({ code: "allowance_exhausted", action: "application", renews_on: "2026-12-01" });
    const dlg = await screen.findByTestId("zero-dialog");
    expect(dlg.textContent).toContain("Your applications for November are used up");
    expect(dlg.textContent).toContain("On 1 December you get 5 applications again.");
    expect(dlg.textContent).toContain("You can keep working on applications you have already started, and download them.");
    expect(dlg.textContent).toContain("To carry on now, you can switch to Jobhunter (20 applications a month) or buy single applications.");
    expect(screen.getByTestId("allowance-close")).toBeTruthy();
    expect(screen.getByTestId("zero-buy").textContent).toBe("Buy applications");
    expect(screen.getByTestId("zero-upgrade").textContent).toBe("Switch to Jobhunter");
  });

  it("largest plan: only Close and Buy, no upgrade sentence", async () => {
    api = mockApi({ current: hunter({ applications: { limit: 20, used: 20, remaining: 0, purchased_remaining: 0, has_plan: true } }) });
    renderWithUser(<TestPlanNotices />);
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    refuse({ code: "allowance_exhausted", action: "application", renews_on: "2026-12-01" });
    const dlg = await screen.findByTestId("zero-dialog");
    expect(dlg.textContent).toContain("On 1 December you get 20 applications again.");
    expect(dlg.textContent).not.toContain("To carry on now");
    expect(screen.queryByTestId("zero-upgrade")).toBeNull();
    expect(screen.getByTestId("zero-buy")).toBeTruthy();
  });

  it("Community allowance: no purchase sentence, only Close", async () => {
    api = mockApi({ current: community({ applications: { limit: 7, used: 7, remaining: 0, purchased_remaining: 0, has_plan: false } }) });
    renderWithUser(<TestPlanNotices />);
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    refuse({ code: "allowance_exhausted", action: "application", renews_on: "2026-12-01" });
    const dlg = await screen.findByTestId("zero-dialog");
    expect(dlg.textContent).toContain("On 1 December you get 7 applications again.");
    expect(dlg.textContent).not.toContain("To carry on now");
    expect(screen.queryByTestId("zero-buy")).toBeNull();
    expect(screen.queryByTestId("zero-upgrade")).toBeNull();
    expect(screen.getByTestId("allowance-close")).toBeTruthy();
  });

  it("German strings match the board", async () => {
    api = mockApi({ current: zero() });
    renderWithUser(<TestPlanNotices />, "de");
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    refuse({ code: "allowance_exhausted", action: "application", renews_on: "2026-12-01" });
    const dlg = await screen.findByTestId("zero-dialog");
    expect(dlg.textContent).toContain("Deine Bewerbungen für November sind aufgebraucht");
    expect(dlg.textContent).toContain("Am 1. Dezember stehen dir wieder 5 Bewerbungen zur Verfügung.");
    expect(dlg.textContent).toContain("Wenn du jetzt weitermachen willst, kannst du zu Jobhunter wechseln (20 Bewerbungen im Monat) oder einzelne Bewerbungen dazukaufen.");
    expect(screen.getByTestId("zero-upgrade").textContent).toBe("Zu Jobhunter wechseln");
  });
});

describe("A8 and unavailable dialogs", () => {
  it("imports, daily limit and unavailable", async () => {
    api = mockApi({ current: allowance() });
    renderWithUser(<TestPlanNotices />, "de");
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    refuse({ code: "allowance_exhausted", action: "profile_import", renews_on: "2026-12-01" });
    expect((await screen.findByTestId("imports-zero-dialog")).textContent).toContain("Deine Lebenslauf-Importe für November sind aufgebraucht");
    await userEvent.click(screen.getByTestId("allowance-close"));
    refuse({ code: "instance_daily_limit", action: null, renews_on: null });
    expect((await screen.findByTestId("daily-limit-dialog")).textContent).toContain("Für heute ist Schluss");
    await userEvent.click(screen.getByTestId("allowance-close"));
    refuse({ code: "allowance_unavailable", action: null, renews_on: null });
    expect((await screen.findByTestId("unavailable-dialog")).textContent).toContain("Gerade geht es nicht weiter");
  });
});

describe("A1 choice", () => {
  it("offerPlanChoice shows A1 without a plan, once per page session after Later", async () => {
    api = mockApi({ current: noPlan() });
    renderWithUser(<TestPlanNotices />);
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    await act(async () => {});
    offerPlanChoice();
    const dlg = await screen.findByTestId("choose-plan-dialog");
    expect(dlg.textContent).toContain("€9.90 / month");
    expect(dlg.textContent).toContain("€24.90 / month");
    expect(dlg.textContent).toContain("Unused applications expire at the end of the month. You can switch to Jobhunter at any time, to Jobseeker from the next month.");
    await userEvent.click(screen.getByTestId("choose-plan-later"));
    expect(screen.queryByTestId("choose-plan-dialog")).toBeNull();
    offerPlanChoice();
    await act(async () => {});
    expect(screen.queryByTestId("choose-plan-dialog")).toBeNull();
    // plan_required always shows it
    refuse({ code: "plan_required", action: null, renews_on: null });
    expect(await screen.findByTestId("choose-plan-dialog")).toBeTruthy();
  });

  it("does not offer when a plan exists", async () => {
    api = mockApi({ current: allowance() });
    renderWithUser(<TestPlanNotices />);
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    await act(async () => {});
    offerPlanChoice();
    await act(async () => {});
    expect(screen.queryByTestId("choose-plan-dialog")).toBeNull();
  });

  it("choosing goes via the summary and PUTs the plan", async () => {
    api = mockApi({ current: noPlan(), putResult: { change: "chosen", allowance: allowance() } });
    renderWithUser(<TestPlanNotices />);
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    await act(async () => {});
    offerPlanChoice();
    await userEvent.click(await screen.findByTestId("plan-choose-jobhunter"));
    const sum = await screen.findByTestId("plan-summary-dialog");
    expect(sum.textContent).toContain("Jobhunter · €24.90 / month");
    expect(screen.getByTestId("plan-summary-due").textContent).toBe("€0.00");
    expect(api.calls.some((c) => c.method === "PUT")).toBe(false);
    await userEvent.click(screen.getByTestId("plan-summary-confirm"));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({ plan_id: "jobhunter" }));
  });
});

describe("A3 start line", () => {
  const cases: [string, ReturnType<typeof allowance>, string][] = [
    ["2 or more left", allowance(), "2 of 5 applications left this month."],
    ["last one", allowance({ applications: { limit: 5, used: 4, remaining: 1, purchased_remaining: 0, has_plan: true } }), "This is your last of 5 applications this month. On 1 December you get 5 again."],
    ["zero, purchased left", zero({ applications: { limit: 5, used: 5, remaining: 0, purchased_remaining: 5, has_plan: true } }), "+ 5 purchased applications"],
    ["zero, none purchased", zero(), "Your applications for November are used up"],
    ["Community allowance", community(), "2 of 5 applications left this month."],
  ];
  it.each(cases)("%s", async (_n, data, text) => {
    api = mockApi({ current: data });
    renderWithUser(<AllowanceStartLine />);
    expect((await screen.findByTestId("allowance-start-line")).textContent).toBe(text);
  });

  it("is silent without a plan on staging, and under policy none", async () => {
    api = mockApi({ current: noPlan() });
    renderWithUser(<AllowanceStartLine />);
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    await act(async () => {});
    expect(screen.queryByTestId("allowance-start-line")).toBeNull();
    cleanup();
    api.restore();
    api = mockApi({ current: community({ policy: "none", applications: null, imports: null }) });
    renderWithUser(<AllowanceStartLine />);
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(0));
    await act(async () => {});
    expect(screen.queryByTestId("allowance-start-line")).toBeNull();
  });
});
