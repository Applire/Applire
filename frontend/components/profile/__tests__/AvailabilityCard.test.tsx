// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Hazelnut b3 WP-V (rulings 787:L3 + V-4, D-B3V7) — the notice period on My
 * Profile: value with "Stand: <date>", nothing shown when empty but the way to
 * add it, edits through PUT /api/profile/availability.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AvailabilityCard } from "../AvailabilityCard";
import { withIntl } from "@/lib/test-utils/with-intl";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AvailabilityCard (D-B3V7)", () => {
  it("shows the value with its date — German copy", () => {
    render(
      withIntl(
        <AvailabilityCard
          value={{ text: "3 Monate zum Quartalsende", recorded_at: "2026-10-22", source: "manual_edit" }}
          apiBase=""
          locale="de"
          onProfileUpdated={() => {}}
        />,
        "de",
      ),
    );
    expect(screen.getByTestId("availability-text")).toHaveTextContent("3 Monate zum Quartalsende");
    expect(screen.getByTestId("availability-recorded")).toHaveTextContent("Stand: 22.10.2026");
    expect(screen.getByTestId("availability-edit")).toHaveTextContent("Bearbeiten");
  });

  it("shows no value and no date when nothing is recorded, only the way to add it", () => {
    render(withIntl(<AvailabilityCard value={null} apiBase="" locale="de" onProfileUpdated={() => {}} />, "de"));
    expect(screen.queryByTestId("availability-text")).toBeNull();
    expect(screen.queryByTestId("availability-recorded")).toBeNull();
    expect(screen.getByTestId("availability-edit")).toHaveTextContent("Kündigungsfrist eintragen");
  });

  it("saves through the one door and hands the profile back", async () => {
    const updated = { profile: { availability: { text: "sofort", recorded_at: "2026-10-22" } } };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(updated), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const onProfileUpdated = vi.fn();
    render(withIntl(<AvailabilityCard value={null} apiBase="" locale="en" onProfileUpdated={onProfileUpdated} />, "en"));
    fireEvent.click(screen.getByTestId("availability-edit"));
    fireEvent.change(screen.getByTestId("availability-input"), { target: { value: "  sofort " } });
    fireEvent.click(screen.getByTestId("availability-save"));
    await waitFor(() => expect(onProfileUpdated).toHaveBeenCalledWith(updated));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/profile/availability");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ text: "sofort" });
  });

  it("a failed save keeps the editor open and says so", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 500 })));
    render(withIntl(<AvailabilityCard value={null} apiBase="" locale="de" onProfileUpdated={() => {}} />, "de"));
    fireEvent.click(screen.getByTestId("availability-edit"));
    fireEvent.click(screen.getByTestId("availability-save"));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByTestId("availability-input")).toBeInTheDocument();
  });
});
