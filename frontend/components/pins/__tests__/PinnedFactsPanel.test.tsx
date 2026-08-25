// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

/**
 * PinnedFactsPanel — E056/ADR-077, US294/US295.
 *
 * A fact pin is a verbatim quote from the candidate's own vault, pinned to
 * one application: it MUST appear in the CV and/or letter (hierarchy:
 * truth > pin > budget). The panel lists current pins and drives a picker
 * dialog that only ever offers the entry's OWN content fields as the quote
 * (clause 1 — a pin carries no free text of its own).
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { PinnedFactsPanel } from "../PinnedFactsPanel";
import { withIntl } from "@/lib/test-utils/with-intl";

const APP_ID = "33333333-3333-3333-3333-333333333333";

const PROFILE = {
  profile: {
    work_experience: [
      {
        id: "w1",
        role: "Engineering Lead",
        company: "Acme GmbH",
        responsibilities: ["Led a team of 8 engineers"],
        achievements: ["Cut deployment time by 90%"],
      },
    ],
    projects: [],
    volunteer_activities: [],
    signature_stories: [],
    skills: [
      { id: "s1", name: "Kubernetes", status: "confirmed" },
      { id: "s2", name: "Astrology", status: "unconfirmed" },
    ],
    certifications: [],
    education: [],
    languages: [],
    publications: [],
  },
};

const STALE_PIN = {
  pin_id: "p1",
  entry_type: "work",
  entry_id: "w1",
  quote: "Led a team of 8 engineers",
  targets: ["cv", "letter"],
  stale: true,
};

function mockFetch(overrides: {
  pins?: unknown[];
  onPost?: (body: unknown) => { status: number; body: unknown };
  onDelete?: () => void;
} = {}) {
  const { pins = [], onPost, onDelete } = overrides;
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "GET" && url === `/api/applications/${APP_ID}`) {
      return { ok: true, json: async () => ({ pinned_facts: pins }) } as Response;
    }
    if (method === "GET" && url === "/api/profile") {
      return { ok: true, json: async () => PROFILE } as Response;
    }
    if (method === "POST" && url === `/api/applications/${APP_ID}/pins`) {
      const body = JSON.parse(init!.body as string);
      const result = onPost
        ? onPost(body)
        : {
            status: 201,
            body: { pin_id: "new-pin", ...body },
          };
      return {
        ok: result.status < 300,
        status: result.status,
        json: async () => result.body,
      } as Response;
    }
    if (method === "DELETE" && url === `/api/applications/${APP_ID}/pins/p1`) {
      onDelete?.();
      return { ok: true, status: 204, json: async () => ({}) } as Response;
    }
    throw new Error(`Unexpected fetch: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("PinnedFactsPanel", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the current pins with a count and a stale badge", async () => {
    mockFetch({ pins: [STALE_PIN] });
    render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));

    await waitFor(() => expect(screen.getByTestId("pinned-fact-p1")).toBeInTheDocument());
    expect(screen.getByTestId("pinned-facts-count").textContent).toContain("1/10");
    expect(screen.getByTestId("pinned-fact-quote-p1").textContent).toBe(
      "Led a team of 8 engineers",
    );
    expect(screen.getByTestId("pinned-fact-stale-p1")).toBeInTheDocument();
  });

  it("shows the empty state when there are no pins", async () => {
    mockFetch({ pins: [] });
    render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
    await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
  });

  it("remove calls DELETE and takes the pin out of the list", async () => {
    const onDelete = vi.fn();
    const fetchMock = mockFetch({ pins: [STALE_PIN], onDelete });
    render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
    await waitFor(() => expect(screen.getByTestId("pinned-fact-p1")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("pinned-fact-remove-p1"));

    await waitFor(() => expect(onDelete).toHaveBeenCalled());
    expect(screen.queryByTestId("pinned-fact-p1")).toBeNull();
    const deleteCall = fetchMock.mock.calls.find(([, init]) => init?.method === "DELETE");
    expect(deleteCall?.[0]).toBe(`/api/applications/${APP_ID}/pins/p1`);
  });

  it("picker: choosing an entry, a quote and targets POSTs and adds the new pin", async () => {
    mockFetch({ pins: [] });
    render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
    await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("pinned-facts-add"));
    expect(screen.getByTestId("pinned-facts-dialog")).toBeInTheDocument();

    await waitFor(() => expect(screen.getByTestId("pin-entry-skill-s1")).toBeInTheDocument());
    // The unconfirmed entry is never claimable — it must not be offered at all.
    expect(screen.queryByTestId("pin-entry-skill-s2")).toBeNull();

    fireEvent.click(screen.getByTestId("pin-entry-skill-s1"));
    fireEvent.click(screen.getByTestId("pin-quote-0"));
    fireEvent.click(screen.getByTestId("pin-dialog-confirm"));

    await waitFor(() => expect(screen.queryByTestId("pinned-facts-dialog")).toBeNull());
    expect(screen.getByTestId("pinned-fact-new-pin")).toBeInTheDocument();
    expect(screen.getByTestId("pinned-fact-quote-new-pin").textContent).toBe("Kubernetes");
  });

  it("picker: a 422 detail from the backend renders as the pin error, dialog stays open", async () => {
    mockFetch({
      pins: [],
      onPost: () => ({ status: 422, body: { detail: "MAX_FACT_PINS cap (10) reached." } }),
    });
    render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
    await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("pinned-facts-add"));
    await waitFor(() => expect(screen.getByTestId("pin-entry-skill-s1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("pin-entry-skill-s1"));
    fireEvent.click(screen.getByTestId("pin-quote-0"));
    fireEvent.click(screen.getByTestId("pin-dialog-confirm"));

    await waitFor(() =>
      expect(screen.getByTestId("pinned-facts-dialog-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("pinned-facts-dialog-error").textContent).toContain(
      "MAX_FACT_PINS cap (10) reached.",
    );
    expect(screen.getByTestId("pinned-facts-dialog")).toBeInTheDocument();
  });

  it("the add button is disabled once the cap of 10 pins is reached", async () => {
    const tenPins = Array.from({ length: 10 }, (_, i) => ({
      ...STALE_PIN,
      pin_id: `p${i}`,
      stale: false,
    }));
    mockFetch({ pins: tenPins });
    render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
    await waitFor(() =>
      expect(screen.getByTestId("pinned-facts-count").textContent).toContain("10/10"),
    );
    expect(screen.getByTestId("pinned-facts-add")).toBeDisabled();
  });
});
