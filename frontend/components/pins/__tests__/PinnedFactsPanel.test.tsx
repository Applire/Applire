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
 * PinnedFactsPanel — E056/ADR-077, US294/US295, plus the 2026-09 UX pass
 * (COPY.md, mock/PanelAfter + Main).
 *
 * A fact pin is a verbatim quote from the candidate's own profile, pinned to
 * one application: it MUST appear in the CV and/or letter (hierarchy:
 * truth > pin > budget). The panel lists current pins and drives a picker
 * that only ever offers the entry's OWN content fields as the quote
 * (clause 1 — a pin carries no free text of its own).
 *
 * What this file pins about the UX pass: the counter only exists once a list
 * exists, the disclosure text is byte-identical to the explainer's, the entry
 * label line appears exactly where the label is NOT the quote, one chip per
 * target carries target AND fate in six states, the teaser is an offer before
 * it is a list, and both add paths pass the first-use explainer.
 */
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { PinnedFactsPanel } from "../PinnedFactsPanel";
import { withIntl } from "@/lib/test-utils/with-intl";
import deMessages from "@/messages/de.json";
import enMessages from "@/messages/en.json";

const APP_ID = "33333333-3333-3333-3333-333333333333";
const CV_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const LETTER_ID = "11111111-1111-1111-1111-111111111111";
const EXPLAINER_ID = "fact_pins_intro";

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
    education: [
      {
        id: "e1",
        degree: "M.Sc. Computer Science",
        institution: "TU München",
        field: "Distributed Systems",
      },
    ],
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

const SKILL_PIN = {
  pin_id: "sp1",
  entry_type: "skill",
  entry_id: "s1",
  quote: "Kubernetes",
  targets: ["cv"],
  stale: false,
};

const EDU_PIN = {
  pin_id: "ep1",
  entry_type: "education",
  entry_id: "e1",
  quote: "Distributed Systems",
  targets: ["cv"],
  stale: false,
};

function settingsBody(dismissed: string[] = []) {
  return {
    default_color_profile_id: null,
    default_accent_hex: null,
    ui_language: "en",
    hide_predownload_notice: false,
    target_cv_pages: null,
    dismissed_explainers: dismissed,
  };
}

// #580: a document's ats-report mock response — "throw" simulates a network
// failure; omitted (undefined) defaults to a pending document (report: null),
// i.e. genuinely "not measured yet", never a stand-in for "not present".
type ReportMockResponse = { ok: boolean; body: unknown } | "throw";

function mockFetch(overrides: {
  pins?: unknown[];
  profile?: unknown;
  onPost?: (body: unknown) => { status: number; body: unknown };
  onDelete?: () => void;
  cvReportResponse?: ReportMockResponse;
  letterReportResponse?: ReportMockResponse;
  dismissedExplainers?: string[];
  onPatch?: (body: unknown) => void;
} = {}) {
  const {
    pins = [],
    profile = PROFILE,
    onPost,
    onDelete,
    cvReportResponse,
    letterReportResponse,
    dismissedExplainers = [],
    onPatch,
  } = overrides;
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/settings")) {
      if (method === "PATCH") {
        onPatch?.(JSON.parse(init!.body as string));
        return { ok: true, status: 200, json: async () => settingsBody(dismissedExplainers) } as Response;
      }
      return { ok: true, json: async () => settingsBody(dismissedExplainers) } as Response;
    }
    if (method === "GET" && url === `/api/applications/${APP_ID}`) {
      return { ok: true, json: async () => ({ pinned_facts: pins }) } as Response;
    }
    if (method === "GET" && url === "/api/profile") {
      return { ok: true, json: async () => profile } as Response;
    }
    if (method === "GET" && url === `/api/cv/${CV_ID}/ats-report`) {
      if (cvReportResponse === "throw") throw new Error("network error");
      const resp = cvReportResponse ?? {
        ok: true,
        body: { document_id: CV_ID, status: "pending", report: null },
      };
      return { ok: resp.ok, json: async () => resp.body } as Response;
    }
    if (method === "GET" && url === `/api/cover-letter/${LETTER_ID}/ats-report`) {
      if (letterReportResponse === "throw") throw new Error("network error");
      const resp = letterReportResponse ?? {
        ok: true,
        body: { document_id: LETTER_ID, status: "pending", report: null },
      };
      return { ok: resp.ok, json: async () => resp.body } as Response;
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

/** Let the mount-time settings/profile/pins reads settle. */
async function settle() {
  await act(async () => {});
  await act(async () => {});
}

describe("PinnedFactsPanel", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the current pins with a stale badge and the promise as its title", async () => {
    mockFetch({ pins: [STALE_PIN] });
    render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));

    await waitFor(() => expect(screen.getByTestId("pinned-fact-p1")).toBeInTheDocument());
    expect(screen.getByTestId("pinned-facts-title").textContent).toBe(
      "Must appear in this document",
    );
    expect(screen.getByTestId("pinned-fact-quote-p1").textContent).toBe(
      "“Led a team of 8 engineers”",
    );
    expect(screen.getByTestId("pinned-fact-stale-p1")).toBeInTheDocument();
  });

  // COPY.md §A — the counter is a statement about a list that exists.
  describe("counter rule", () => {
    it("shows no counter and the empty line when there are no pins", async () => {
      mockFetch({ pins: [] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      expect(screen.queryByTestId("pinned-facts-count")).toBeNull();
      expect(screen.getByTestId("pinned-facts-empty").textContent).toBe("No fact pinned yet.");
    });

    it("shows the counter and no empty line from one pin onwards", async () => {
      mockFetch({ pins: [STALE_PIN] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-count")).toBeInTheDocument());
      expect(screen.getByTestId("pinned-facts-count").textContent).toBe(
        "1 of max. 10 · this application only",
      );
      expect(screen.queryByTestId("pinned-facts-empty")).toBeNull();
    });

    it("counts up to the cap and disables the add button there", async () => {
      const tenPins = Array.from({ length: 10 }, (_, i) => ({
        ...STALE_PIN,
        pin_id: `p${i}`,
        stale: false,
      }));
      mockFetch({ pins: tenPins });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() =>
        expect(screen.getByTestId("pinned-facts-count").textContent).toContain("10 of max. 10"),
      );
      expect(screen.getByTestId("pinned-facts-add")).toBeDisabled();
      // The tooltip interpolates {max} — without the param next-intl renders
      // its error fallback and the user is told nothing.
      expect(screen.getByTestId("pinned-facts-add").getAttribute("title")).toBe(
        "Maximum of 10 pinned facts reached",
      );
    });
  });

  // D-5 — one explanation, two places to meet it.
  describe('"Wie funktioniert das?" disclosure', () => {
    it("starts collapsed and toggles the body", async () => {
      mockFetch({ pins: [] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());

      const toggle = screen.getByTestId("pinned-facts-how-toggle");
      expect(toggle.getAttribute("aria-expanded")).toBe("false");
      expect(screen.queryByTestId("pinned-facts-how-body")).toBeNull();

      fireEvent.click(toggle);
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
      expect(screen.getByTestId("pinned-facts-how-body")).toBeInTheDocument();

      fireEvent.click(toggle);
      expect(screen.queryByTestId("pinned-facts-how-body")).toBeNull();
    });

    it("renders exactly the explainer's three paragraphs, byte for byte", async () => {
      mockFetch({ pins: [] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      fireEvent.click(screen.getByTestId("pinned-facts-how-toggle"));
      expect(screen.getByTestId("pinned-facts-how-body").textContent).toBe(
        enMessages.gaps.pins.howBody,
      );
    });

    // The catalog is the contract: if the two ever drift, the panel and the
    // explainer would tell the same user two different stories.
    it.each(["de", "en"] as const)(
      "%s: gaps.pins.howBody is the three explainer paragraphs joined",
      (locale) => {
        const messages = locale === "de" ? deMessages : enMessages;
        const { p1, p2, p3 } = messages.explainers.factPins;
        expect(messages.gaps.pins.howBody).toBe([p1, p2, p3].join(" "));
      },
    );
  });

  // COPY.md §A — the label says WHERE the sentence comes from; for a skill or
  // a certification the label IS the sentence, so it would only repeat itself.
  describe("entry label line", () => {
    it("renders the role · company line under a work pin's quote", async () => {
      mockFetch({ pins: [STALE_PIN] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-entry-label-p1")).toBeInTheDocument(),
      );
      expect(screen.getByTestId("pinned-fact-entry-label-p1").textContent).toBe(
        "Engineering Lead · Acme GmbH",
      );
    });

    it("renders the degree · institution line under an education pin's quote", async () => {
      mockFetch({ pins: [EDU_PIN] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-entry-label-ep1")).toBeInTheDocument(),
      );
      expect(screen.getByTestId("pinned-fact-entry-label-ep1").textContent).toBe(
        "M.Sc. Computer Science · TU München",
      );
    });

    it("omits the label line for a skill pin, whose label IS the quote", async () => {
      mockFetch({ pins: [SKILL_PIN] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-fact-sp1")).toBeInTheDocument());
      await settle();
      expect(screen.queryByTestId("pinned-fact-entry-label-sp1")).toBeNull();
    });

    it("omits the label line when the profile could not be read", async () => {
      const fn = vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        if (url.endsWith("/api/settings")) {
          return { ok: true, json: async () => settingsBody([]) } as Response;
        }
        if (url === `/api/applications/${APP_ID}`) {
          return { ok: true, json: async () => ({ pinned_facts: [STALE_PIN] }) } as Response;
        }
        if (url === "/api/profile") throw new Error("network error");
        throw new Error(`Unexpected fetch: ${method} ${url}`);
      });
      vi.stubGlobal("fetch", fn);
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-fact-p1")).toBeInTheDocument());
      await settle();
      expect(screen.queryByTestId("pinned-fact-entry-label-p1")).toBeNull();
      // The pin itself is unaffected — provenance is a nicety, the promise is not.
      expect(screen.getByTestId("pinned-fact-quote-p1").textContent).toContain(
        "Led a team of 8 engineers",
      );
    });
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

  it("shows the load error when the application read fails", async () => {
    const fn = vi.fn(async (url: string) => {
      if (url.endsWith("/api/settings")) {
        return { ok: true, json: async () => settingsBody([]) } as Response;
      }
      if (url === "/api/profile") {
        return { ok: true, json: async () => PROFILE } as Response;
      }
      return { ok: false, status: 500, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fn);
    render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
    await waitFor(() =>
      expect(screen.getByTestId("pinned-facts-load-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("pinned-facts-load-error").textContent).toBe(
      "Pinned facts could not be loaded.",
    );
  });

  // #679 / COPY.md §D — the explainer stands in front of BOTH add paths, once.
  describe("first-use explainer gating", () => {
    it("panel add button opens the explainer, then the picker on continue", async () => {
      mockFetch({ pins: [], dismissedExplainers: [] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      expect(screen.getByTestId(`explainer-${EXPLAINER_ID}`)).toBeInTheDocument();
      expect(screen.queryByTestId("pinned-facts-dialog")).toBeNull();

      fireEvent.click(screen.getByTestId(`explainer-${EXPLAINER_ID}-continue`));
      expect(screen.queryByTestId(`explainer-${EXPLAINER_ID}`)).toBeNull();
      expect(screen.getByTestId("pinned-facts-dialog")).toBeInTheDocument();
    });

    it("teaser add button opens the explainer too", async () => {
      mockFetch({ pins: [], dismissedExplainers: [] });
      render(
        withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" variant="teaser" />),
      );
      await waitFor(() =>
        expect(screen.getByTestId("pinned-facts-teaser-add")).toBeInTheDocument(),
      );
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-teaser-add"));
      expect(screen.getByTestId(`explainer-${EXPLAINER_ID}`)).toBeInTheDocument();
    });

    it("cancel on the explainer closes it AND leaves the picker shut", async () => {
      mockFetch({ pins: [], dismissedExplainers: [] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      fireEvent.click(screen.getByTestId(`explainer-${EXPLAINER_ID}-cancel`));
      expect(screen.queryByTestId(`explainer-${EXPLAINER_ID}`)).toBeNull();
      expect(screen.queryByTestId("pinned-facts-dialog")).toBeNull();
    });

    it("goes straight to the picker once the explainer is dismissed", async () => {
      mockFetch({ pins: [], dismissedExplainers: [EXPLAINER_ID] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      expect(screen.queryByTestId(`explainer-${EXPLAINER_ID}`)).toBeNull();
      expect(screen.getByTestId("pinned-facts-dialog")).toBeInTheDocument();
    });

    it("PATCHes the dismissal only when the checkbox was ticked", async () => {
      const patches: unknown[] = [];
      mockFetch({ pins: [], dismissedExplainers: [], onPatch: (b) => patches.push(b) });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      fireEvent.click(screen.getByTestId(`explainer-${EXPLAINER_ID}-dontshowagain-input`));
      fireEvent.click(screen.getByTestId(`explainer-${EXPLAINER_ID}-continue`));
      await waitFor(() => expect(patches).toEqual([{ dismiss_explainer: EXPLAINER_ID }]));
    });
  });

  describe("picker", () => {
    it("choosing an entry, a quote and targets POSTs and adds the new pin", async () => {
      mockFetch({ pins: [], dismissedExplainers: [EXPLAINER_ID] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      expect(screen.getByTestId("pinned-facts-dialog")).toBeInTheDocument();
      expect(screen.getByTestId("pinned-facts-dialog-intro").textContent).toBe(
        "The fact is taken word for word and never cut from this application.",
      );

      await waitFor(() => expect(screen.getByTestId("pin-entry-work-w1")).toBeInTheDocument());
      // The unconfirmed entry is never claimable — it must not be offered at all.
      expect(screen.queryByTestId("pin-entry-skill-s2")).toBeNull();

      fireEvent.click(screen.getByTestId("pin-entry-work-w1"));
      fireEvent.click(screen.getByTestId("pin-quote-0"));
      fireEvent.click(screen.getByTestId("pin-dialog-confirm"));

      await waitFor(() => expect(screen.queryByTestId("pinned-facts-dialog")).toBeNull());
      expect(screen.getByTestId("pinned-fact-new-pin")).toBeInTheDocument();
      expect(screen.getByTestId("pinned-fact-quote-new-pin").textContent).toBe(
        "“Led a team of 8 engineers”",
      );
    });

    // D-6 — an entry with exactly one statement has nothing to choose between.
    it("skips the statement step for a single-statement entry and preselects it", async () => {
      let postedBody: { quote?: string } | null = null;
      mockFetch({
        pins: [],
        dismissedExplainers: [EXPLAINER_ID],
        onPost: (body) => {
          postedBody = body as { quote?: string };
          return { status: 201, body: { pin_id: "new-pin", ...(body as object) } };
        },
      });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      await waitFor(() => expect(screen.getByTestId("pin-entry-skill-s1")).toBeInTheDocument());
      fireEvent.click(screen.getByTestId("pin-entry-skill-s1"));

      // No radio list, no "Which statement" heading — the statement is shown
      // as a static line and the confirm button is live immediately.
      expect(screen.queryByTestId("pin-quote-0")).toBeNull();
      expect(screen.getByTestId("pinned-facts-dialog-single-quote").textContent).toBe(
        "Kubernetes",
      );
      // …and the entry label is not printed above a statement identical to it.
      expect(
        screen.getByTestId("pinned-facts-dialog-quotes").textContent!.match(/Kubernetes/g),
      ).toHaveLength(1);
      expect(screen.getByTestId("pin-dialog-confirm")).not.toBeDisabled();

      fireEvent.click(screen.getByTestId("pin-dialog-confirm"));
      await waitFor(() => expect(screen.queryByTestId("pinned-facts-dialog")).toBeNull());
      expect(postedBody).toMatchObject({ quote: "Kubernetes" });
    });

    it("keeps the statement step for a multi-statement entry", async () => {
      mockFetch({ pins: [], dismissedExplainers: [EXPLAINER_ID] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      await waitFor(() => expect(screen.getByTestId("pin-entry-work-w1")).toBeInTheDocument());
      fireEvent.click(screen.getByTestId("pin-entry-work-w1"));

      expect(screen.queryByTestId("pinned-facts-dialog-single-quote")).toBeNull();
      expect(screen.getByTestId("pin-quote-0")).toBeInTheDocument();
      expect(screen.getByTestId("pin-quote-1")).toBeInTheDocument();
      // Nothing chosen yet: the confirm button must not pretend otherwise.
      expect(screen.getByTestId("pin-dialog-confirm")).toBeDisabled();
    });

    it("shows the targets hint under the checkboxes", async () => {
      mockFetch({ pins: [], dismissedExplainers: [EXPLAINER_ID] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      await waitFor(() => expect(screen.getByTestId("pin-entry-skill-s1")).toBeInTheDocument());
      fireEvent.click(screen.getByTestId("pin-entry-skill-s1"));
      expect(screen.getByTestId("pin-targets-hint").textContent).toBe(
        "Applies to this application only. After generating, the review report shows whether it worked.",
      );
    });

    it("a 422 detail from the backend renders as the pin error, dialog stays open", async () => {
      mockFetch({
        pins: [],
        dismissedExplainers: [EXPLAINER_ID],
        onPost: () => ({ status: 422, body: { detail: "MAX_FACT_PINS cap (10) reached." } }),
      });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      await waitFor(() => expect(screen.getByTestId("pin-entry-skill-s1")).toBeInTheDocument());
      fireEvent.click(screen.getByTestId("pin-entry-skill-s1"));
      fireEvent.click(screen.getByTestId("pin-dialog-confirm"));

      await waitFor(() =>
        expect(screen.getByTestId("pinned-facts-dialog-error")).toBeInTheDocument(),
      );
      expect(screen.getByTestId("pinned-facts-dialog-error").textContent).toContain(
        "MAX_FACT_PINS cap (10) reached.",
      );
      expect(screen.getByTestId("pinned-facts-dialog")).toBeInTheDocument();
    });

    // Finding #1 (adversarial pass, 2026-09-06): the duplicate-pin ValueError
    // (backend/applire/services/fact_pins.py:210, mapped to 422 by add_pin)
    // was rendered verbatim — raw English inside the German UI. 422 is shared
    // with the cap/claim-gate/quote-resolution errors above, which keep their
    // raw passthrough; only THIS exact backend detail gets localised.
    it("a duplicate-pin 422 renders the localised message, not the raw backend text, dialog stays open", async () => {
      mockFetch({
        pins: [],
        dismissedExplainers: [EXPLAINER_ID],
        onPost: () => ({
          status: 422,
          body: { detail: "This fact is already pinned on this application." },
        }),
      });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />, "de"));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      await waitFor(() => expect(screen.getByTestId("pin-entry-skill-s1")).toBeInTheDocument());
      fireEvent.click(screen.getByTestId("pin-entry-skill-s1"));
      fireEvent.click(screen.getByTestId("pin-dialog-confirm"));

      await waitFor(() =>
        expect(screen.getByTestId("pinned-facts-dialog-error")).toBeInTheDocument(),
      );
      const errorText = screen.getByTestId("pinned-facts-dialog-error").textContent;
      expect(errorText).toContain("Dieser Fakt ist für diese Bewerbung schon festgelegt.");
      expect(errorText).not.toContain("already pinned on this application");
      expect(screen.getByTestId("pinned-facts-dialog")).toBeInTheDocument();
    });
  });

  // COPY.md §B — before generation the control is an offer, not a list (D-2:
  // "Bearbeiten" expands inline, no navigation, no modal).
  describe("teaser variant", () => {
    it("empty: kicker, question, body and the CTA — no panel", async () => {
      mockFetch({ pins: [] });
      render(
        withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" variant="teaser" />),
      );
      await waitFor(() =>
        expect(screen.getByTestId("pinned-facts-teaser")).toBeInTheDocument(),
      );
      expect(screen.getByTestId("pinned-facts-teaser").textContent).toContain(
        "Are there facts that must appear in your documents?",
      );
      expect(screen.getByTestId("pinned-facts-teaser-add").textContent).toBe("Pin facts");
      expect(screen.queryByTestId("pinned-facts-panel")).toBeNull();
      expect(screen.queryByTestId("pinned-facts-teaser-count")).toBeNull();
    });

    it("with pins: the count and an Edit that expands the panel inline", async () => {
      mockFetch({ pins: [STALE_PIN, SKILL_PIN] });
      render(
        withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" variant="teaser" />),
      );
      await waitFor(() =>
        expect(screen.getByTestId("pinned-facts-teaser-count")).toBeInTheDocument(),
      );
      expect(screen.getByTestId("pinned-facts-teaser-count").textContent).toBe("2 facts pinned");
      expect(screen.queryByTestId("pinned-facts-teaser-add")).toBeNull();
      expect(screen.queryByTestId("pinned-facts-panel")).toBeNull();

      const edit = screen.getByTestId("pinned-facts-teaser-edit");
      expect(edit.getAttribute("aria-expanded")).toBe("false");
      fireEvent.click(edit);

      expect(edit.getAttribute("aria-expanded")).toBe("true");
      // Inline, inside the same card — not a route change, not a dialog.
      expect(screen.getByTestId("pinned-facts-teaser")).toContainElement(
        screen.getByTestId("pinned-facts-panel"),
      );
      expect(screen.getByTestId("pinned-fact-p1")).toBeInTheDocument();
      // The expanded panel carries the add button (COPY.md §B).
      expect(screen.getByTestId("pinned-facts-add")).toBeInTheDocument();

      fireEvent.click(edit);
      expect(screen.queryByTestId("pinned-facts-panel")).toBeNull();
    });

    it("uses the singular count form for exactly one pin", async () => {
      mockFetch({ pins: [STALE_PIN] });
      render(
        withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" variant="teaser" />),
      );
      await waitFor(() =>
        expect(screen.getByTestId("pinned-facts-teaser-count")).toBeInTheDocument(),
      );
      expect(screen.getByTestId("pinned-facts-teaser-count").textContent).toBe("1 fact pinned");
    });

    it("localises the teaser into German", async () => {
      mockFetch({ pins: [] });
      render(
        withIntl(
          <PinnedFactsPanel applicationId={APP_ID} apiBase="" variant="teaser" />,
          "de",
        ),
      );
      await waitFor(() =>
        expect(screen.getByTestId("pinned-facts-teaser-add")).toBeInTheDocument(),
      );
      expect(screen.getByTestId("pinned-facts-teaser-add").textContent).toBe("Fakten festlegen");
      expect(screen.getByTestId("pinned-facts-teaser").textContent).toContain(
        "Optional, vor dem Generieren",
      );
    });

    // Finding #2 (adversarial pass, 2026-09-06): a failed pins fetch left
    // `pinList` at [] with `loadError` true, and the teaser's empty branch
    // only checked `pinList.length === 0` — an unknown state rendered
    // byte-identical to "zero pins", inviting the user to pin on top of it.
    it("a failed pins fetch renders the load-error text and no CTA", async () => {
      const fn = vi.fn(async (url: string) => {
        if (url.endsWith("/api/settings")) {
          return { ok: true, json: async () => settingsBody([]) } as Response;
        }
        if (url === "/api/profile") {
          return { ok: true, json: async () => PROFILE } as Response;
        }
        return { ok: false, status: 500, json: async () => ({}) } as Response;
      });
      vi.stubGlobal("fetch", fn);
      render(
        withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" variant="teaser" />),
      );
      await waitFor(() =>
        expect(screen.getByTestId("pinned-facts-load-error")).toBeInTheDocument(),
      );
      expect(screen.getByTestId("pinned-facts-load-error").textContent).toBe(
        "Pinned facts could not be loaded.",
      );
      expect(screen.queryByTestId("pinned-facts-teaser-add")).toBeNull();
    });
  });

  // #580 + COPY.md §A — ONE chip per target, carrying target AND fate. Six
  // states, both locales: a document that does not exist yet is not a fate.
  describe("target/fate chips (#580)", () => {
    const PIN = {
      pin_id: "p1",
      entry_type: "work",
      entry_id: "w1",
      quote: "Led a team of 8 engineers",
      targets: ["cv", "letter"],
      stale: false,
    };

    function reportBody(documentId: string, pinnedFacts: unknown[] | null) {
      return {
        document_id: documentId,
        status: "ready",
        report:
          pinnedFacts === null
            ? null
            : { checks: [], keywords: { present: [], missing: [] }, pinned_facts: pinnedFacts },
      };
    }

    function pinEntry(extra: Record<string, unknown> = {}) {
      return [
        {
          pin_id: "p1",
          entry_type: "work",
          quote: PIN.quote,
          present: false,
          stale: false,
          ...extra,
        },
      ];
    }

    async function renderWithCvReport(
      pinnedFacts: unknown[] | null,
      locale: "de" | "en" = "en",
    ) {
      mockFetch({
        pins: [PIN],
        cvReportResponse: { ok: true, body: reportBody(CV_ID, pinnedFacts) },
      });
      render(
        withIntl(
          <PinnedFactsPanel applicationId={APP_ID} apiBase="" cvId={CV_ID} />,
          locale,
        ),
      );
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-fate-cv-p1")).toBeInTheDocument(),
      );
      await settle();
      return screen.getByTestId("pinned-fact-fate-cv-p1");
    }

    it("state 1 (no document yet): the chip names the target only", async () => {
      mockFetch({ pins: [PIN] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-fact-p1")).toBeInTheDocument());
      expect(screen.getByTestId("pinned-fact-target-cv-p1").textContent).toBe("CV");
      expect(screen.getByTestId("pinned-fact-target-letter-p1").textContent).toBe(
        "Cover letter",
      );
      // Nothing measured means nothing claimed — no fate chip at all.
      expect(screen.queryByTestId("pinned-fact-fate-cv-p1")).toBeNull();
      expect(screen.queryByTestId("pinned-fact-fate-letter-p1")).toBeNull();
    });

    it("state 2 (present)", async () => {
      const chip = await renderWithCvReport(pinEntry({ present: true }));
      expect(chip.textContent).toBe("CV · included");
      expect(chip.className).toContain("bg-success-container");
    });

    it("state 3 (unmet)", async () => {
      const chip = await renderWithCvReport(pinEntry());
      expect(chip.textContent).toBe("CV · not included");
      expect(chip.className).toContain("bg-critical-container");
    });

    it("state 4 (unmet + ledger conflict)", async () => {
      const chip = await renderWithCvReport(pinEntry({ ledger_conflict: ["microservices"] }));
      expect(chip.textContent).toBe("CV · not included — do-not-claim term: microservices");
    });

    it("state 5 (removed by the truth floor) outranks the plain unmet text", async () => {
      const chip = await renderWithCvReport(
        pinEntry({ removed_by_truth_floor: true, ledger_conflict: ["microservices"] }),
      );
      expect(chip.textContent).toBe("CV · removed by the truthfulness check");
      expect(chip.textContent).not.toContain("not included");
    });

    it("state 6 (not measured): report null", async () => {
      const chip = await renderWithCvReport(null);
      expect(chip.textContent).toBe("CV · not measured yet");
      expect(chip.className).toContain("bg-surface-container");
    });

    it("state 6 (not measured): pin absent from the report", async () => {
      const chip = await renderWithCvReport([]);
      expect(chip.textContent).toBe("CV · not measured yet");
    });

    it("state 6 (not measured): the report fetch failed", async () => {
      mockFetch({ pins: [PIN], cvReportResponse: "throw" });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" cvId={CV_ID} />));
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toBe(
          "CV · not measured yet",
        ),
      );
    });

    it("measures the CV and the letter independently for the same pin", async () => {
      mockFetch({
        pins: [PIN],
        cvReportResponse: {
          ok: true,
          body: reportBody(CV_ID, [
            { pin_id: "p1", entry_type: "work", quote: PIN.quote, present: true, stale: false },
          ]),
        },
        letterReportResponse: {
          ok: true,
          body: reportBody(LETTER_ID, [
            { pin_id: "p1", entry_type: "work", quote: PIN.quote, present: false, stale: false },
          ]),
        },
      });
      render(
        withIntl(
          <PinnedFactsPanel
            applicationId={APP_ID}
            apiBase=""
            cvId={CV_ID}
            coverLetterId={LETTER_ID}
          />,
        ),
      );
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toBe("CV · included"),
      );
      expect(screen.getByTestId("pinned-fact-fate-letter-p1").textContent).toBe(
        "Cover letter · not included",
      );
    });

    describe("German", () => {
      it("state 1 (no document yet)", async () => {
        mockFetch({ pins: [PIN] });
        render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />, "de"));
        await waitFor(() => expect(screen.getByTestId("pinned-fact-p1")).toBeInTheDocument());
        expect(screen.getByTestId("pinned-fact-target-cv-p1").textContent).toBe("Lebenslauf");
        expect(screen.getByTestId("pinned-fact-target-letter-p1").textContent).toBe(
          "Anschreiben",
        );
      });

      it("state 2 (present)", async () => {
        const chip = await renderWithCvReport(pinEntry({ present: true }), "de");
        expect(chip.textContent).toBe("Lebenslauf · enthalten");
      });

      it("state 3 (unmet)", async () => {
        const chip = await renderWithCvReport(pinEntry(), "de");
        expect(chip.textContent).toBe("Lebenslauf · nicht enthalten");
      });

      it("state 4 (unmet + ledger conflict)", async () => {
        const chip = await renderWithCvReport(
          pinEntry({ ledger_conflict: ["Microservices"] }),
          "de",
        );
        expect(chip.textContent).toBe(
          "Lebenslauf · nicht enthalten — Nicht-behaupten-Begriff: Microservices",
        );
      });

      it("state 5 (removed by the truth floor)", async () => {
        const chip = await renderWithCvReport(pinEntry({ removed_by_truth_floor: true }), "de");
        expect(chip.textContent).toBe("Lebenslauf · durch Wahrheitsprüfung entfernt");
      });

      it("state 6 (not measured)", async () => {
        const chip = await renderWithCvReport(null, "de");
        expect(chip.textContent).toBe("Lebenslauf · noch nicht gemessen");
      });

      it("wraps the quote in German quotation marks", async () => {
        mockFetch({ pins: [PIN] });
        render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />, "de"));
        await waitFor(() => expect(screen.getByTestId("pinned-fact-p1")).toBeInTheDocument());
        expect(screen.getByTestId("pinned-fact-quote-p1").textContent).toBe(
          "„Led a team of 8 engineers“",
        );
      });
    });
  });

  // #580 — the picker refuses a `cv` target for entry types the CV template
  // never renders (volunteer, publication), mirroring the backend's 422
  // client-side so the user never has to hit the error.
  describe("picker CV target gate (#580)", () => {
    const VOLUNTEER_PROFILE = {
      profile: {
        ...PROFILE.profile,
        volunteer_activities: [
          {
            id: "v1",
            role: "Mentor",
            organization: "Coding for Kids",
            responsibilities: ["Mentored 5 students"],
          },
        ],
      },
    };

    function mockFetchWithVolunteer(onPost: (body: unknown) => void) {
      const fn = vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        if (url.endsWith("/api/settings")) {
          return { ok: true, json: async () => settingsBody([EXPLAINER_ID]) } as Response;
        }
        if (method === "GET" && url === `/api/applications/${APP_ID}`) {
          return { ok: true, json: async () => ({ pinned_facts: [] }) } as Response;
        }
        if (method === "GET" && url === "/api/profile") {
          return { ok: true, json: async () => VOLUNTEER_PROFILE } as Response;
        }
        if (method === "POST" && url === `/api/applications/${APP_ID}/pins`) {
          const body = JSON.parse(init!.body as string);
          onPost(body);
          return { ok: true, status: 201, json: async () => ({ pin_id: "new-pin", ...body }) } as Response;
        }
        throw new Error(`Unexpected fetch: ${method} ${url}`);
      });
      vi.stubGlobal("fetch", fn);
      return fn;
    }

    it("disables and unchecks the CV target for a volunteer entry, showing the hint", async () => {
      mockFetchWithVolunteer(() => {});
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      await waitFor(() => expect(screen.getByTestId("pin-entry-volunteer-v1")).toBeInTheDocument());
      fireEvent.click(screen.getByTestId("pin-entry-volunteer-v1"));

      // Single statement, but the label is NOT the statement — it stays.
      expect(screen.getByTestId("pinned-facts-dialog-quotes").textContent).toContain(
        "Mentor · Coding for Kids",
      );
      const cvCheckbox = screen.getByTestId("pin-target-cv") as HTMLInputElement;
      expect(cvCheckbox.disabled).toBe(true);
      expect(cvCheckbox.checked).toBe(false);
      expect(screen.getByTestId("pin-target-cv-unavailable-hint")).toBeInTheDocument();
    });

    it("POSTs targets: [\"letter\"] for a volunteer entry, never cv", async () => {
      let postedBody: { entry_type?: string; targets?: string[] } | null = null;
      mockFetchWithVolunteer((body) => {
        postedBody = body as { entry_type?: string; targets?: string[] };
      });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
      await settle();

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      await waitFor(() => expect(screen.getByTestId("pin-entry-volunteer-v1")).toBeInTheDocument());
      fireEvent.click(screen.getByTestId("pin-entry-volunteer-v1"));
      // Single statement (D-6): the quote step is skipped, the gate is not.
      fireEvent.click(screen.getByTestId("pin-dialog-confirm"));

      await waitFor(() => expect(screen.queryByTestId("pinned-facts-dialog")).toBeNull());
      expect(postedBody).toMatchObject({ entry_type: "volunteer", targets: ["letter"] });
    });
  });
});
