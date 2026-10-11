// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { withIntl } from "@/lib/test-utils/with-intl";

const refreshTheme = vi.fn();
vi.mock("@/components/theme-provider", () => ({ useTheme: () => ({ refreshTheme }) }));

import { AppearancePicker } from "../AppearancePicker";

const scheme = (id: string, name: string, isDefault = false) => ({
  id,
  name,
  seed_primary: "#1b4f72",
  seed_accent: "#2a8f9d",
  seed_secondary: "#c9a84c",
  derived: {},
  is_default: isDefault,
});

function mockChoices(selected: string | null, defaultId = "d") {
  return vi.spyOn(global, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/api/color-schemes") && !init?.method) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          default_id: defaultId,
          selected_id: selected,
          schemes: [scheme("d", "EU Blue", true), scheme("f", "Forest")],
        }),
      } as Response;
    }
    return { ok: true, status: 200, json: async () => ({}) } as Response;
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  refreshTheme.mockClear();
});

describe("AppearancePicker (US357)", () => {
  it("offers the instance default plus every other offered scheme — the default once", async () => {
    mockChoices(null);
    render(withIntl(<AppearancePicker />));
    const radios = await screen.findAllByRole("radio");
    expect(radios).toHaveLength(2);
    expect(screen.getByText("Instance default")).toBeTruthy();
    expect(screen.getByText(/Currently EU Blue/)).toBeTruthy();
    expect(screen.getByTestId("appearance-option-default").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("appearance-option-f").getAttribute("aria-checked")).toBe("false");
  });

  it("choosing a scheme stores it and repaints at once", async () => {
    const spy = mockChoices(null);
    render(withIntl(<AppearancePicker />));
    await userEvent.click(await screen.findByTestId("appearance-option-f"));
    await waitFor(() => expect(refreshTheme).toHaveBeenCalledTimes(1));
    const patch = spy.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PATCH");
    expect(JSON.parse(String((patch![1] as RequestInit).body))).toEqual({ ui_color_scheme_id: "f" });
    expect(screen.getByTestId("appearance-option-f").getAttribute("aria-checked")).toBe("true");
  });

  it("going back to the default stores null, so the person follows the admin's default", async () => {
    const spy = mockChoices("f");
    render(withIntl(<AppearancePicker />));
    await userEvent.click(await screen.findByTestId("appearance-option-default"));
    await waitFor(() => expect(refreshTheme).toHaveBeenCalled());
    const patch = spy.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PATCH");
    expect(JSON.parse(String((patch![1] as RequestInit).body))).toEqual({ ui_color_scheme_id: null });
  });

  it("a stored choice equal to the default reads as 'default' (SF-THEME.2)", async () => {
    mockChoices("d");
    render(withIntl(<AppearancePicker />));
    expect((await screen.findByTestId("appearance-option-default")).getAttribute("aria-checked")).toBe("true");
  });

  it("renders in German", async () => {
    mockChoices(null);
    render(withIntl(<AppearancePicker />, "de"));
    expect(await screen.findByText("Standard der Instanz")).toBeTruthy();
  });
});

describe("AppearancePicker — a malformed answer", () => {
  it("shows the load-failed line instead of crashing the page", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({ ok: true, status: 200, json: async () => [] } as Response);
    render(withIntl(<AppearancePicker />));
    expect(await screen.findByText("The color schemes could not be loaded.")).toBeTruthy();
  });
});
