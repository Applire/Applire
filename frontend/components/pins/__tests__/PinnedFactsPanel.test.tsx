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
const CV_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const LETTER_ID = "11111111-1111-1111-1111-111111111111";

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

// #580: a document's ats-report mock response — "throw" simulates a network
// failure; omitted (undefined) defaults to a pending document (report: null),
// i.e. genuinely "not measured yet", never a stand-in for "not present".
type ReportMockResponse = { ok: boolean; body: unknown } | "throw";

function mockFetch(overrides: {
  pins?: unknown[];
  onPost?: (body: unknown) => { status: number; body: unknown };
  onDelete?: () => void;
  cvReportResponse?: ReportMockResponse;
  letterReportResponse?: ReportMockResponse;
} = {}) {
  const { pins = [], onPost, onDelete, cvReportResponse, letterReportResponse } = overrides;
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "GET" && url === `/api/applications/${APP_ID}`) {
      return { ok: true, json: async () => ({ pinned_facts: pins }) } as Response;
    }
    if (method === "GET" && url === "/api/profile") {
      return { ok: true, json: async () => PROFILE } as Response;
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

  it("renders the JF-F-I.1/JF-F-I.5 subtitle once, regardless of pin count", async () => {
    mockFetch({ pins: [] });
    render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
    await waitFor(() => expect(screen.getByTestId("pinned-facts-empty")).toBeInTheDocument());
    expect(screen.getByTestId("pinned-facts-subtitle").textContent).toContain(
      "Pins beat the length budget, never the truthfulness check.",
    );
    expect(screen.getAllByTestId("pinned-facts-subtitle")).toHaveLength(1);
  });

  // #580 — per-document fate markers on the pin control itself: a pin's
  // presence/absence measured against the CV and/or letter's OWN ATS report,
  // never a stand-in state for a document that doesn't exist yet.
  describe("fate markers (#580)", () => {
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

    it("shows a success chip when the pin is present in the document", async () => {
      mockFetch({
        pins: [PIN],
        cvReportResponse: {
          ok: true,
          body: reportBody(CV_ID, [
            { pin_id: "p1", entry_type: "work", quote: PIN.quote, present: true, stale: false },
          ]),
        },
      });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" cvId={CV_ID} />));
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toBe("in the CV"),
      );
    });

    it("shows a critical chip when the pin is unmet, with no do-not-claim term", async () => {
      mockFetch({
        pins: [PIN],
        cvReportResponse: {
          ok: true,
          body: reportBody(CV_ID, [
            { pin_id: "p1", entry_type: "work", quote: PIN.quote, present: false, stale: false },
          ]),
        },
      });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" cvId={CV_ID} />));
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toBe("not in the CV"),
      );
    });

    it("appends the do-not-claim term when unmet with a ledger conflict", async () => {
      mockFetch({
        pins: [PIN],
        cvReportResponse: {
          ok: true,
          body: reportBody(CV_ID, [
            {
              pin_id: "p1",
              entry_type: "work",
              quote: PIN.quote,
              present: false,
              stale: false,
              ledger_conflict: ["microservices"],
            },
          ]),
        },
      });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" cvId={CV_ID} />));
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toContain(
          "not in the CV",
        ),
      );
      expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toContain(
        "do-not-claim term: microservices",
      );
    });

    it("shows the removed-by-truth-floor text, taking precedence over the plain unmet text", async () => {
      mockFetch({
        pins: [PIN],
        cvReportResponse: {
          ok: true,
          body: reportBody(CV_ID, [
            {
              pin_id: "p1",
              entry_type: "work",
              quote: PIN.quote,
              present: false,
              stale: false,
              removed_by_truth_floor: true,
              ledger_conflict: ["microservices"],
            },
          ]),
        },
      });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" cvId={CV_ID} />));
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toBe(
          "removed by the truthfulness check",
        ),
      );
      // The plain "not in the CV" / do-not-claim wording must NOT also appear.
      expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).not.toContain(
        "not in the CV",
      );
    });

    it("shows a neutral not-measured chip when the document's report is null", async () => {
      mockFetch({
        pins: [PIN],
        cvReportResponse: { ok: true, body: reportBody(CV_ID, null) },
      });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" cvId={CV_ID} />));
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toBe("not measured yet"),
      );
    });

    it("shows a neutral not-measured chip when the pin has no entry in the report yet", async () => {
      mockFetch({
        pins: [PIN],
        cvReportResponse: { ok: true, body: reportBody(CV_ID, []) },
      });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" cvId={CV_ID} />));
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toBe("not measured yet"),
      );
    });

    it("shows a neutral not-measured chip when the report fetch fails", async () => {
      mockFetch({ pins: [PIN], cvReportResponse: "throw" });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" cvId={CV_ID} />));
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toBe("not measured yet"),
      );
    });

    it("renders no fate chip for a target with no document id at all", async () => {
      mockFetch({ pins: [PIN] });
      render(withIntl(<PinnedFactsPanel applicationId={APP_ID} apiBase="" />));
      await waitFor(() => expect(screen.getByTestId("pinned-fact-p1")).toBeInTheDocument());
      expect(screen.queryByTestId("pinned-fact-fate-cv-p1")).toBeNull();
      expect(screen.queryByTestId("pinned-fact-fate-letter-p1")).toBeNull();
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
        expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toBe("in the CV"),
      );
      expect(screen.getByTestId("pinned-fact-fate-letter-p1").textContent).toBe(
        "not in the cover letter",
      );
    });

    it("localises fate chips into German", async () => {
      mockFetch({
        pins: [PIN],
        cvReportResponse: {
          ok: true,
          body: reportBody(CV_ID, [
            {
              pin_id: "p1",
              entry_type: "work",
              quote: PIN.quote,
              present: false,
              stale: false,
              ledger_conflict: ["Microservices"],
            },
          ]),
        },
      });
      render(
        withIntl(
          <PinnedFactsPanel applicationId={APP_ID} apiBase="" cvId={CV_ID} />,
          "de",
        ),
      );
      await waitFor(() =>
        expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toContain(
          "nicht im Lebenslauf",
        ),
      );
      expect(screen.getByTestId("pinned-fact-fate-cv-p1").textContent).toContain(
        "Nicht-behaupten-Begriff: Microservices",
      );
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

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      await waitFor(() => expect(screen.getByTestId("pin-entry-volunteer-v1")).toBeInTheDocument());
      fireEvent.click(screen.getByTestId("pin-entry-volunteer-v1"));

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

      fireEvent.click(screen.getByTestId("pinned-facts-add"));
      await waitFor(() => expect(screen.getByTestId("pin-entry-volunteer-v1")).toBeInTheDocument());
      fireEvent.click(screen.getByTestId("pin-entry-volunteer-v1"));
      fireEvent.click(screen.getByTestId("pin-quote-0"));
      fireEvent.click(screen.getByTestId("pin-dialog-confirm"));

      await waitFor(() => expect(screen.queryByTestId("pinned-facts-dialog")).toBeNull());
      expect(postedBody).toMatchObject({ entry_type: "volunteer", targets: ["letter"] });
    });
  });
});
