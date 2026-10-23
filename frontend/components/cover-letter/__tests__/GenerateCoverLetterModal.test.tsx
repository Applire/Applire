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

import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import {
  GenerateCoverLetterModal,
  LETTER_FIELD_MAX,
  LETTER_MOTIVATION_MAX,
} from "../GenerateCoverLetterModal";
import { guardedNavigate } from "@/components/document/useUnsavedDraftGuard";

const mockPush = vi.fn();
const mockRouter = { push: mockPush, replace: vi.fn() };
vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
}));

const BASE_PROPS = {
  jobId: "job-1",
  onClose: vi.fn(),
  onGenerated: vi.fn(),
};

function renderModal() {
  return render(withIntl(<GenerateCoverLetterModal {...BASE_PROPS} />, "de"));
}

describe("GenerateCoverLetterModal", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // #311: the failure path threw new Error("Generation failed"), and because
  // that is always an Error instance, err.message displaced the
  // t("errorGenerationFailed") fallback sitting right beside it — English text
  // in the German modal.
  it("shows the translated error when the backend sends no detail", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({}),
    } as Response);

    renderModal();
    fireEvent.click(screen.getByTestId("cl-modal-generate"));

    await waitFor(() => expect(screen.getByText("Unbekannter Fehler")).toBeTruthy());
    expect(screen.queryByText("Generation failed")).toBeNull();
  });

  it("still prefers a deliberately worded backend detail", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ detail: "Profil unvollständig." }),
    } as Response);

    renderModal();
    fireEvent.click(screen.getByTestId("cl-modal-generate"));

    await waitFor(() => expect(screen.getByText("Profil unvollständig.")).toBeTruthy());
  });

  // Error detail hardening: a 500 answers {error_code, message, error_id}; the
  // modal shows its translated fallback instead of throwing on `.trim()`.
  it("shows the translated error when a 500 answers a structured detail", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      status: 500,
      json: () =>
        Promise.resolve({
          detail: { error_code: "internal_error", message: "An unexpected error occurred.", error_id: "abc123def456" },
        }),
    } as Response);

    renderModal();
    fireEvent.click(screen.getByTestId("cl-modal-generate"));

    await waitFor(() => expect(screen.getByText("Unbekannter Fehler")).toBeTruthy());
  });

  it("renders the tonality options in German", () => {
    renderModal();
    expect(screen.getByText("Förmlich")).toBeTruthy();
    expect(screen.getByText("Professionell")).toBeTruthy();
    expect(screen.getByText("Locker")).toBeTruthy();
  });
});

// Frontend collector #604 — "the cover-letter creation modal leaves the recipient
// company blank even though the field is labelled '(aus Stellenanzeige)' and
// job_analyses.company_name is already known". A confidence gap, not a functional
// one: the backend re-derives the company independently, so the letter is right
// while the form looks ignorant.
//
// The mechanism already existed and no caller fed it — `prefillRecipientCompany`
// is declared on the props and is passed by none of the three call sites
// (cv/page.tsx, cover-letter/page.tsx x2). Fixing it at each call site would need
// each page to fetch the job analysis; fixing it HERE fixes all three at once,
// and `GET /api/job/{id}` (JobAnalysisResponse.company_name) is the endpoint that
// actually carries the field — the flow state's JobAnalysisSummary does NOT, it
// has only job_id and role_title.
describe("GenerateCoverLetterModal — recipient company prefill (#604)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function jobResponse(company: string | null) {
    return {
      ok: true,
      json: () => Promise.resolve({ id: "job-1", role_title: "Senior Engineer", company_name: company }),
    } as Response;
  }

  it("fills the company from the job analysis", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jobResponse("Rheinwerk Verpackungen GmbH"));
    renderModal();
    await waitFor(() =>
      expect(screen.getByTestId("cl-recipient-company")).toHaveValue("Rheinwerk Verpackungen GmbH"),
    );
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/job/job-1"));
  });

  it("does not fetch when a caller already supplied the company", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue(jobResponse("Von der API"));
    render(
      withIntl(
        <GenerateCoverLetterModal {...BASE_PROPS} prefillRecipientCompany="Vom Aufrufer GmbH" />,
        "de",
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("cl-recipient-company")).toHaveValue("Vom Aufrufer GmbH"),
    );
    // (the profile read for the notice period is a separate, unconditional request)
    expect(fetchSpy).not.toHaveBeenCalledWith(expect.stringContaining("/api/job/"));
  });

  it("does not overwrite a regenerate's stored recipient company", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue(jobResponse("Von der API"));
    render(
      withIntl(
        <GenerateCoverLetterModal
          {...BASE_PROPS}
          existingInputs={{ recipient_company: "Beim letzten Mal GmbH" }}
        />,
        "de",
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("cl-recipient-company")).toHaveValue("Beim letzten Mal GmbH"),
    );
    // (the profile read for the notice period is a separate, unconditional request)
    expect(fetchSpy).not.toHaveBeenCalledWith(expect.stringContaining("/api/job/"));
  });

  it("leaves the field empty when the analysis knows no company", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jobResponse(null));
    renderModal();
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.getByTestId("cl-recipient-company")).toHaveValue("");
  });

  it("stays usable when the lookup fails", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new Error("network"));
    renderModal();
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.getByTestId("cl-recipient-company")).toHaveValue("");
    expect(screen.queryByText("Unbekannter Fehler")).toBeNull();
  });

  it("never clobbers what the user has already typed", async () => {
    // The lookup is async; a fast typist must win it.
    let resolve!: (v: Response) => void;
    vi.spyOn(global, "fetch").mockReturnValue(
      new Promise<Response>((r) => {
        resolve = r;
      }) as ReturnType<typeof global.fetch>,
    );
    renderModal();
    const field = screen.getByTestId("cl-recipient-company");
    fireEvent.change(field, { target: { value: "Selbst getippt AG" } });
    resolve(jobResponse("Rheinwerk Verpackungen GmbH"));
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(field).toHaveValue("Selbst getippt AG");
  });
});

// Founder ruling L-1 (Hazelnut build 3, #787): the two optional prose fields are
// named for what a German reader expects in a letter — notice period and the
// reason for the change — sit above the salary, and no longer promise that the
// AI fills them in (the writer never invents these values, ruling D1-1).
describe("GenerateCoverLetterModal — notice period and reason (L-1)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function profileWith(availability: { text: string } | null) {
    return {
      ok: true,
      json: () => Promise.resolve({ profile: availability ? { availability } : {} }),
    } as Response;
  }

  function mockFetch(profile: Response | Promise<Response>) {
    return vi.spyOn(global, "fetch").mockImplementation((async (url: string) => {
      if (String(url).includes("/api/profile")) return profile;
      return { ok: true, json: () => Promise.resolve({ company_name: null }) } as Response;
    }) as typeof fetch);
  }

  it("uses the new German labels and no AI promise", () => {
    mockFetch(profileWith(null));
    renderModal();
    expect(screen.getByText("Kündigungsfrist / frühester Eintritt")).toBeTruthy();
    expect(screen.getByText("Warum diese Stelle, warum der Wechsel?")).toBeTruthy();
    expect(screen.queryByText(/KI generiert/)).toBeNull();
    expect(screen.getByTestId("cl-motivation").getAttribute("placeholder")).not.toMatch(/KI|Leer lassen/);
  });

  it("uses the new English labels and no AI promise", () => {
    mockFetch(profileWith(null));
    render(withIntl(<GenerateCoverLetterModal {...BASE_PROPS} />, "en"));
    expect(screen.getByText("Notice period / earliest start")).toBeTruthy();
    expect(screen.getByText("Why this role, why the change?")).toBeTruthy();
    expect(screen.getByTestId("cl-motivation").getAttribute("placeholder")).not.toMatch(/AI generates|Leave blank/);
  });

  it("puts notice period and reason above the salary", () => {
    mockFetch(profileWith(null));
    renderModal();
    const order = ["cl-availability", "cl-motivation", "cl-salary"].map((id) => screen.getByTestId(id));
    for (let i = 0; i < order.length - 1; i++) {
      expect(order[i].compareDocumentPosition(order[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("pre-fills the notice period from the profile and says so", async () => {
    mockFetch(profileWith({ text: "3 Monate zum Quartalsende" }));
    renderModal();
    await waitFor(() => expect(screen.getByTestId("cl-availability")).toHaveValue("3 Monate zum Quartalsende"));
    expect(screen.getByText("aus deinem Profil")).toBeTruthy();
  });

  it("drops the profile hint as soon as the user edits the field", async () => {
    mockFetch(profileWith({ text: "3 Monate zum Quartalsende" }));
    renderModal();
    await screen.findByText("aus deinem Profil");
    fireEvent.change(screen.getByTestId("cl-availability"), { target: { value: "sofort" } });
    expect(screen.queryByText("aus deinem Profil")).toBeNull();
    expect(screen.getByTestId("cl-availability")).toHaveValue("sofort");
  });

  it("does not pre-fill when the application already has its own value", async () => {
    const spy = mockFetch(profileWith({ text: "3 Monate zum Quartalsende" }));
    render(
      withIntl(
        <GenerateCoverLetterModal {...BASE_PROPS} existingInputs={{ availability: "ab 1. Januar" }} />,
        "de",
      ),
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId("cl-availability")).toHaveValue("ab 1. Januar");
    expect(screen.queryByText("aus deinem Profil")).toBeNull();
    expect(spy).not.toHaveBeenCalledWith(expect.stringContaining("/api/profile"));
  });

  it("leaves the field empty and silent when the profile read fails", async () => {
    const spy = mockFetch({ ok: false, status: 404, json: () => Promise.resolve({}) } as Response);
    renderModal();
    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.stringContaining("/api/profile")));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId("cl-availability")).toHaveValue("");
    expect(screen.queryByText("aus deinem Profil")).toBeNull();
    expect(screen.queryByText("Unbekannter Fehler")).toBeNull();
  });

  it("does not overwrite what the user typed while the profile was loading", async () => {
    let resolve!: (v: Response) => void;
    mockFetch(new Promise<Response>((r) => { resolve = r; }));
    renderModal();
    fireEvent.change(screen.getByTestId("cl-availability"), { target: { value: "sofort" } });
    resolve(profileWith({ text: "3 Monate" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId("cl-availability")).toHaveValue("sofort");
    expect(screen.queryByText("aus deinem Profil")).toBeNull();
  });

  it("keeps the request body fields unchanged and reports what was submitted", async () => {
    const onGenerated = vi.fn();
    const spy = vi.spyOn(global, "fetch").mockImplementation((async (url: string) => {
      if (String(url).includes("/api/cover-letter/generate")) {
        return { ok: true, json: () => Promise.resolve({ cover_letter_id: "cl-9" }) } as Response;
      }
      if (String(url).includes("/api/profile")) return profileWith({ text: "3 Monate" });
      return { ok: true, json: () => Promise.resolve({}) } as Response;
    }) as typeof fetch);
    render(withIntl(<GenerateCoverLetterModal {...BASE_PROPS} onGenerated={onGenerated} />, "de"));
    await screen.findByText("aus deinem Profil");
    fireEvent.change(screen.getByTestId("cl-motivation"), { target: { value: "Neuer Fokus" } });
    fireEvent.click(screen.getByTestId("cl-modal-generate"));
    await waitFor(() => expect(onGenerated).toHaveBeenCalled());
    const call = spy.mock.calls.find((c) => String(c[0]).includes("/generate"))!;
    const body = JSON.parse((call[1] as RequestInit).body as string);
    expect(body.availability).toBe("3 Monate");
    expect(body.motivation).toBe("Neuer Fokus");
    expect(Object.keys(body).sort()).toEqual(
      // B3-11: the form always sends the checkbox explicitly (FL↔FF contract).
      ["availability", "job_id", "motivation", "recipient_company", "recipient_name", "salary",
        "save_availability_to_profile", "tone"],
    );
    expect(onGenerated).toHaveBeenCalledWith("cl-9", expect.objectContaining({ availability: "3 Monate" }));
  });
});

// B3-11 + MD3-4 — "Auch im Profil speichern" under the notice period (default
// ON, sent explicitly) and the input caps, the same numbers as the backend's
// LETTER_INPUT_MAX_CHARS (backend/applire/constants.py).
describe("GenerateCoverLetterModal — save to profile (B3-11) and caps (MD3-4)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function mockGenerate() {
    return vi.spyOn(global, "fetch").mockImplementation((async (url: string) => {
      if (String(url).includes("/api/cover-letter/generate")) {
        return { ok: true, json: () => Promise.resolve({ cover_letter_id: "cl-1" }) } as Response;
      }
      return { ok: true, json: () => Promise.resolve({}) } as Response;
    }) as typeof fetch);
  }

  async function submittedBody(spy: ReturnType<typeof mockGenerate>, onGenerated: ReturnType<typeof vi.fn>) {
    fireEvent.click(screen.getByTestId("cl-modal-generate"));
    await waitFor(() => expect(onGenerated).toHaveBeenCalled());
    const call = spy.mock.calls.find((c) => String(c[0]).includes("/generate"))!;
    return JSON.parse((call[1] as RequestInit).body as string);
  }

  it("the checkbox sits under the notice period, ticked, and its label is German", () => {
    mockGenerate();
    renderModal();
    const box = screen.getByTestId("cl-save-availability") as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(screen.getByText("Auch im Profil speichern")).toBeTruthy();
    const order = ["cl-availability", "cl-save-availability", "cl-motivation"].map((id) => screen.getByTestId(id));
    for (let i = 0; i < order.length - 1; i++) {
      expect(order[i].compareDocumentPosition(order[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("sends save_availability_to_profile: true by default", async () => {
    const spy = mockGenerate();
    const onGenerated = vi.fn();
    render(withIntl(<GenerateCoverLetterModal {...BASE_PROPS} onGenerated={onGenerated} />, "de"));
    fireEvent.change(screen.getByTestId("cl-availability"), { target: { value: "3 Monate zum Quartalsende" } });
    const body = await submittedBody(spy, onGenerated);
    expect(body.save_availability_to_profile).toBe(true);
    expect(body.availability).toBe("3 Monate zum Quartalsende");
  });

  it("sends save_availability_to_profile: false when unticked", async () => {
    const spy = mockGenerate();
    const onGenerated = vi.fn();
    render(withIntl(<GenerateCoverLetterModal {...BASE_PROPS} onGenerated={onGenerated} />, "en"));
    expect(screen.getByText("Also save to my profile")).toBeTruthy();
    fireEvent.change(screen.getByTestId("cl-availability"), { target: { value: "available immediately" } });
    fireEvent.click(screen.getByTestId("cl-save-availability"));
    const body = await submittedBody(spy, onGenerated);
    expect(body.save_availability_to_profile).toBe(false);
  });

  it("caps every free-text input: motivation 2,000, the rest 300", () => {
    mockGenerate();
    renderModal();
    expect(LETTER_MOTIVATION_MAX).toBe(2000);
    expect(LETTER_FIELD_MAX).toBe(300);
    expect(screen.getByTestId("cl-motivation").getAttribute("maxLength")).toBe("2000");
    for (const id of ["cl-recipient-name", "cl-recipient-company", "cl-availability", "cl-salary"]) {
      expect(screen.getByTestId(id).getAttribute("maxLength")).toBe("300");
    }
  });

  it("counts the motivation against its cap", () => {
    mockGenerate();
    renderModal();
    fireEvent.change(screen.getByTestId("cl-motivation"), { target: { value: "Zahlungsverkehr" } });
    expect(screen.getByTestId("cl-motivation-count").textContent).toBe("15 / 2.000 Zeichen");
  });
});

// MD3-1 (adversarial A-5) — typed form inputs are a draft: the shell nav, the
// back button and a reload ask first, as the section editor does.
describe("GenerateCoverLetterModal — unsent form guard (MD3-1)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockPush.mockReset();
  });

  function unloadPrevented(): boolean {
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  }

  function mockQuiet(profileText: string | null = null) {
    return vi.spyOn(global, "fetch").mockImplementation((async (url: string) => {
      if (String(url).includes("/api/cover-letter/generate")) {
        return { ok: true, json: () => Promise.resolve({ cover_letter_id: "cl-2" }) } as Response;
      }
      if (String(url).includes("/api/profile")) {
        return {
          ok: true,
          json: () => Promise.resolve({ profile: profileText ? { availability: { text: profileText } } : {} }),
        } as Response;
      }
      return { ok: true, json: () => Promise.resolve({ company_name: "NovaPay GmbH" }) } as Response;
    }) as typeof fetch);
  }

  it("what the form filled in itself is not a draft", async () => {
    mockQuiet("3 Monate zum Quartalsende");
    renderModal();
    await waitFor(() => expect(screen.getByTestId("cl-availability")).toHaveValue("3 Monate zum Quartalsende"));
    await waitFor(() => expect(screen.getByTestId("cl-recipient-company")).toHaveValue("NovaPay GmbH"));
    expect(unloadPrevented()).toBe(false);
    const push = vi.fn();
    guardedNavigate("/dashboard", push);
    expect(push).toHaveBeenCalledWith("/dashboard");
  });

  it("a typed motivation holds a reload and asks before the shell nav leaves", async () => {
    mockQuiet();
    renderModal();
    fireEvent.change(screen.getByTestId("cl-motivation"), { target: { value: "Ich will in den Zahlungsverkehr." } });
    await waitFor(() => expect(unloadPrevented()).toBe(true));
    const push = vi.fn();
    act(() => guardedNavigate("/dashboard", push));
    expect(push).not.toHaveBeenCalled();
    const dialog = await screen.findByTestId("edit-unsaved-dialog");
    expect(dialog.textContent).toContain("Angaben noch nicht verwendet");
    fireEvent.click(screen.getByTestId("edit-unsaved-stay"));
    expect(screen.getByTestId("cl-motivation")).toHaveValue("Ich will in den Zahlungsverkehr.");
    act(() => guardedNavigate("/dashboard", push));
    fireEvent.click(await screen.findByTestId("edit-unsaved-discard"));
    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("the back button asks (popstate on the guard entry)", async () => {
    mockQuiet();
    renderModal();
    fireEvent.change(screen.getByTestId("cl-availability"), { target: { value: "sofort" } });
    await waitFor(() => expect(unloadPrevented()).toBe(true));
    act(() => {
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByTestId("edit-unsaved-dialog")).toBeInTheDocument();
  });

  it("generating uses the inputs: the guard is gone before the page moves on", async () => {
    mockQuiet();
    const back = vi.spyOn(window.history, "back").mockImplementation(() => {
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    const onGenerated = vi.fn(() => {
      // The page navigates here; no draft guard may still hold it.
      expect(back).toHaveBeenCalled();
    });
    render(withIntl(<GenerateCoverLetterModal {...BASE_PROPS} onGenerated={onGenerated} />, "de"));
    fireEvent.change(screen.getByTestId("cl-motivation"), { target: { value: "Neuer Fokus" } });
    await waitFor(() => expect(unloadPrevented()).toBe(true));
    fireEvent.click(screen.getByTestId("cl-modal-generate"));
    await waitFor(() => expect(onGenerated).toHaveBeenCalled());
  });
});
