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
 * PersonalInfoEditor — US292 slice B. Each `it` name below is annotated with
 * the FMEA/build rule it covers.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PersonalInfoEditor } from "../PersonalInfoEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { PersonalInfo } from "@/lib/profile-entries";

function jsonResponse(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as unknown as Response;
}

const FULL_INFO: PersonalInfo = {
  name: "Anna Bauer",
  email: "anna@example.com",
  phone: "+49 30 1234567",
  location: "Berlin",
  address: "Musterstraße 1, 10115 Berlin",
  nationality: "Deutsch",
  date_of_birth: "1990-02-01",
  linkedin_url: "https://linkedin.com/in/annabauer",
  xing_url: "https://xing.com/profile/AnnaBauer",
  website_url: "https://annabauer.dev",
};

function renderEditor(value: PersonalInfo | null | undefined, onProfileUpdated = vi.fn()) {
  render(
    withIntl(
      <PersonalInfoEditor
        value={value}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
    ),
  );
  return { onProfileUpdated };
}

describe("PersonalInfoEditor", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  it("sends only the changed keys", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: FULL_INFO } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-phone"), {
      target: { value: "+49 30 7654321" },
    });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ phone: "+49 30 7654321" });
  });

  // photo_url is PhotoManager's field — never rendered as an input here, and
  // never present in a patch even if it is present on the incoming value.
  it("never renders or sends photo_url, even when present on the value", async () => {
    const withPhoto = { ...FULL_INFO, photo_url: "https://cdn.example.com/photo.jpg" } as PersonalInfo;
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: withPhoto } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(withPhoto);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    expect(screen.queryByTestId("personal-info-field-photo-url")).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId("personal-info-field-phone"), { target: { value: "+49 30 000" } });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.prototype.hasOwnProperty.call(body, "photo_url")).toBe(false);
  });

  it("normalises a blanked-out field to null, not an empty string", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: FULL_INFO } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-nationality"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ nationality: null });
  });

  it("refuses to submit when the name is blank or whitespace-only", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-name"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    expect(await screen.findByTestId("personal-info-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts date_of_birth as DD.MM.YYYY and sends it as ISO (what the backend stores)", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: FULL_INFO } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor({ ...FULL_INFO, date_of_birth: null });

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-date-of-birth"), {
      target: { value: "01.02.1990" },
    });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ date_of_birth: "1990-02-01" });
  });

  it("accepts date_of_birth as D.M.YYYY (single-digit day/month) and sends it zero-padded ISO", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: FULL_INFO } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor({ ...FULL_INFO, date_of_birth: null });

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-date-of-birth"), {
      target: { value: "1.2.1990" },
    });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ date_of_birth: "1990-02-01" });
  });

  it("accepts date_of_birth as ISO YYYY-MM-DD and sends it unchanged", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: FULL_INFO } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor({ ...FULL_INFO, date_of_birth: null });

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-date-of-birth"), {
      target: { value: "1990-02-01" },
    });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ date_of_birth: "1990-02-01" });
  });

  it("rejects an unparseable date_of_birth with the validation message and sends no request", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-date-of-birth"), {
      target: { value: "Feb 1990" },
    });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    expect(await screen.findByTestId("personal-info-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // A merge-patch must not echo back a key it did not edit — an unknown
  // stray key on `value` (never tracked by FIELD_KEYS) must never appear.
  it("never echoes an unknown key present on the value", async () => {
    const withUnknown = { ...FULL_INFO, some_legacy_field: "leftover" } as PersonalInfo;
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: withUnknown } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(withUnknown);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-phone"), { target: { value: "+49 30 000" } });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.prototype.hasOwnProperty.call(body, "some_legacy_field")).toBe(false);
    expect(body).toEqual({ phone: "+49 30 000" });
  });

  it("closes without a request when nothing changed", () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.click(screen.getByTestId("personal-info-save"));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("personal-info-dialog")).not.toBeInTheDocument();
  });

  // H1.6 (409) — on stale_edit, no retry; reload `current`; dialog stays open; notice shown.
  it("on a 409 stale_edit, reloads from `current`, keeps the dialog open, and shows a notice", async () => {
    const current = { updated_at: "t3", profile: { personal_info: FULL_INFO } };
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-phone"), { target: { value: "+49 30 000" } });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(screen.getByTestId("personal-info-stale-notice")).toBeInTheDocument());
    expect(screen.getByTestId("personal-info-dialog")).toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows the backend message on a 422", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(422, { detail: "date_of_birth is invalid" }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-phone"), { target: { value: "+49 30 000" } });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    expect(await screen.findByTestId("personal-info-dialog-error")).toHaveTextContent(
      "date_of_birth is invalid",
    );
  });

  // H0.4 — a 200 with every sent key coming back unchanged surfaces the notice.
  // H0.4 — a German-typed date comes back ISO from the backend; that is a
  // normalisation, not a lost write, so no mismatch notice may appear.
  it("shows NO mismatch notice when a DD.MM.YYYY date comes back as ISO", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, {
        updated_at: "t2",
        profile: { personal_info: { ...FULL_INFO, date_of_birth: "1990-02-01" } },
      }),
    );
    global.fetch = fetchMock;
    renderEditor({ ...FULL_INFO, date_of_birth: null });

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-date-of-birth"), {
      target: { value: "01.02.1990" },
    });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.queryByTestId("personal-info-dialog")).not.toBeInTheDocument(),
    );
    expect(screen.queryByTestId("personal-info-mismatch-notice")).not.toBeInTheDocument();
  });

  it("shows a mismatch notice when the saved keys come back unchanged", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: FULL_INFO } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-phone"), { target: { value: "+49 30 000" } });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    expect(await screen.findByTestId("personal-info-mismatch-notice")).toBeInTheDocument();
  });

  it("sends exactly one PATCH on a rapid double-click of Save", async () => {
    let resolveFetch!: (value: Response) => void;
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-phone"), { target: { value: "+49 30 000" } });
    fireEvent.click(screen.getByTestId("personal-info-save"));
    fireEvent.click(screen.getByTestId("personal-info-save"));

    resolveFetch(jsonResponse(200, { updated_at: "t2", profile: { personal_info: FULL_INFO } }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  // H1.13-equivalent — the focus effect must be keyed on [dialogOpen] only.
  it("keeps focus in the email field while typing there", () => {
    renderEditor(FULL_INFO);
    fireEvent.click(screen.getByTestId("personal-info-edit"));
    const emailField = screen.getByTestId("personal-info-field-email") as HTMLInputElement;
    emailField.focus();
    expect(document.activeElement).toBe(emailField);

    fireEvent.change(emailField, { target: { value: "a" } });
    fireEvent.change(emailField, { target: { value: "an" } });
    fireEvent.change(emailField, { target: { value: "ann" } });

    expect(document.activeElement).toBe(emailField);
  });

  // F2 (major) — a failed save leaves focus outside the dialog; Escape must
  // still close it via a document-level listener.
  it("closes the dialog on a document-level Escape after a failed (422) save", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(422, { detail: "phone is invalid" }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-phone"), { target: { value: "+49 30 000" } });
    fireEvent.click(screen.getByTestId("personal-info-save"));
    await screen.findByTestId("personal-info-dialog-error");

    fireEvent.keyDown(document.body, { key: "Escape" });

    expect(screen.queryByTestId("personal-info-dialog")).not.toBeInTheDocument();
  });

  // F3 — a well-formed but impossible calendar date ("31.02.1990") passes the
  // shape regex; only a round-trip through the ISO form catches it.
  it("rejects a well-formed but impossible calendar date (31.02.1990)", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-date-of-birth"), {
      target: { value: "31.02.1990" },
    });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    expect(await screen.findByTestId("personal-info-validation-error")).toHaveTextContent(
      "Please enter a valid date",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts 29.02.2024 (leap year)", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: FULL_INFO } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor({ ...FULL_INFO, date_of_birth: null });

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-date-of-birth"), {
      target: { value: "29.02.2024" },
    });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ date_of_birth: "2024-02-29" });
  });

  it("rejects 29.02.2023 (not a leap year)", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-date-of-birth"), {
      target: { value: "29.02.2023" },
    });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    expect(await screen.findByTestId("personal-info-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // F3 — a raw Pydantic validation dump must never reach the user verbatim;
  // sectionSave.ts blanks it out, so the editor falls back to its generic error.
  it("shows the generic error, not a raw Pydantic dump, on a 422 validation-error response", async () => {
    const dump =
      "1 validation error for MasterProfileData\npersonal_info.date_of_birth\n  Input should be a valid date [type=date_from_datetime_parsing]";
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(422, { detail: dump }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-phone"), { target: { value: "+49 30 000" } });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    const errorEl = await screen.findByTestId("personal-info-dialog-error");
    expect(errorEl.textContent).not.toContain("validation error for");
    expect(errorEl.textContent).not.toContain("MasterProfileData");
    expect(errorEl).toHaveTextContent("The section could not be saved. Please try again.");
  });

  // F5 (minor) — a space inside the local part is an obviously mistyped email.
  it("rejects an email with a space (\"us er@example.com\")", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-email"), {
      target: { value: "us er@example.com" },
    });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    expect(await screen.findByTestId("personal-info-validation-error")).toHaveTextContent(
      "valid email address",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts a well-formed email", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: FULL_INFO } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-email"), {
      target: { value: "new@example.com" },
    });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ email: "new@example.com" });
  });

  it("leaves an emptied email allowed (no validation error)", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { personal_info: FULL_INFO } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor(FULL_INFO);

    fireEvent.click(screen.getByTestId("personal-info-edit"));
    fireEvent.change(screen.getByTestId("personal-info-field-email"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("personal-info-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("personal-info-validation-error")).not.toBeInTheDocument();
  });
});
