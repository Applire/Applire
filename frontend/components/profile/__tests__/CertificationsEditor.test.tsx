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
 * CertificationsEditor — US291. Each `it` name below is annotated with the
 * FMEA build rule it covers.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CertificationsEditor } from "../CertificationsEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { Certification } from "@/lib/profile-entries";

const FULL_CERTIFICATION: Certification = {
  id: "c1",
  name: "AWS Solutions Architect",
  issuing_organization: "Amazon Web Services",
  date_obtained: "2023-04",
  expiry_date: "2026-04",
  credential_id: "AWS-123",
  credential_url: "https://example.com/cred/AWS-123",
  status: "confirmed",
};

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function renderEditor(entries: Certification[], onProfileUpdated = vi.fn()) {
  render(
    withIntl(
      <CertificationsEditor
        entries={entries}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
    ),
  );
  return { onProfileUpdated };
}

describe("CertificationsEditor", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  // H1.1 — an existing entry's id round-trips verbatim in the PATCH body.
  it("echoes the certification id verbatim when editing an existing entry", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { certifications: [FULL_CERTIFICATION] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_CERTIFICATION]);

    fireEvent.click(screen.getByTestId("certification-edit-0"));
    fireEvent.click(screen.getByTestId("certification-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body[0].id).toBe("c1");
  });

  // H1.1 — a brand-new entry is sent WITHOUT an id key at all.
  // H2.2 (PO ruling 2026-08-25) — a NEW certification carries an explicit
  // status: "confirmed", and the add affordance reads "Add as confirmed".
  it("sends a new certification without an id key and with an explicit confirmed status", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { certifications: [] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    expect(screen.getByTestId("certifications-add")).toHaveTextContent("Add as confirmed");
    fireEvent.click(screen.getByTestId("certifications-add"));
    fireEvent.change(screen.getByTestId("certification-field-name"), {
      target: { value: "Scrum Master" },
    });
    fireEvent.click(screen.getByTestId("certification-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.prototype.hasOwnProperty.call(body[0], "id")).toBe(false);
    expect(body[0].name).toBe("Scrum Master");
    expect(body[0].status).toBe("confirmed");
  });

  // H2.1 — an existing entry keeps whatever status it carries; an unrelated
  // edit leaves an unconfirmed certification unconfirmed.
  it("leaves an unconfirmed certification's status untouched on an unrelated edit", async () => {
    const unconfirmed: Certification = { ...FULL_CERTIFICATION, status: "unconfirmed" };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { certifications: [unconfirmed] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([unconfirmed]);

    expect(screen.getByTestId("status-badge")).toHaveTextContent("unconfirmed");
    fireEvent.click(screen.getByTestId("certification-edit-0"));
    fireEvent.change(screen.getByTestId("certification-field-credential-id"), {
      target: { value: "AWS-999" },
    });
    fireEvent.click(screen.getByTestId("certification-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.status).toBe("unconfirmed");
    expect(sent.credential_id).toBe("AWS-999");
  });

  // H1.3 — editing one field leaves every other populated field byte-identical.
  it("patches only the edited field, preserving every other populated field", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { certifications: [FULL_CERTIFICATION] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_CERTIFICATION]);

    fireEvent.click(screen.getByTestId("certification-edit-0"));
    fireEvent.change(screen.getByTestId("certification-field-credential-url"), {
      target: { value: "https://example.com/cred/AWS-999" },
    });
    fireEvent.click(screen.getByTestId("certification-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    const { credential_url: sentUrl, ...sentRest } = sent;
    const { credential_url: origUrl, ...origRest } = FULL_CERTIFICATION;
    expect(sentRest).toEqual(origRest);
    expect(sentUrl).toBe("https://example.com/cred/AWS-999");
    expect(origUrl).toBe("https://example.com/cred/AWS-123");
  });

  // H2.3 — a stored YYYY-MM-DD parses back to month+year fine: the picker
  // shows 2023/04, and re-selecting through it emits the canonical YYYY-MM.
  it("parses a YYYY-MM-DD date_obtained into the picker and re-emits it as YYYY-MM", async () => {
    const withFullDate: Certification = { ...FULL_CERTIFICATION, date_obtained: "2023-04-15" };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { certifications: [withFullDate] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([withFullDate]);

    fireEvent.click(screen.getByTestId("certification-edit-0"));
    expect((screen.getByTestId("certification-date-obtained-year") as HTMLInputElement).value).toBe("2023");
    expect((screen.getByTestId("certification-date-obtained-month") as HTMLSelectElement).value).toBe("04");
    // Re-select the (already-shown) month to drive it through the picker's
    // emit path rather than leaving the original string untouched.
    fireEvent.change(screen.getByTestId("certification-date-obtained-month"), { target: { value: "04" } });
    fireEvent.click(screen.getByTestId("certification-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.date_obtained).toBe("2023-04");
  });

  // H1.12-equivalent — a date the picker CAN parse but the user never
  // touches survives an unrelated edit byte-identical (whole-object spread).
  it("preserves a YYYY-MM-DD date_obtained verbatim when only an unrelated field is edited", async () => {
    const withFullDate: Certification = { ...FULL_CERTIFICATION, date_obtained: "2023-04-15" };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { certifications: [withFullDate] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([withFullDate]);

    fireEvent.click(screen.getByTestId("certification-edit-0"));
    fireEvent.change(screen.getByTestId("certification-field-credential-id"), {
      target: { value: "AWS-999" },
    });
    fireEvent.click(screen.getByTestId("certification-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.date_obtained).toBe("2023-04-15");
  });

  // H2.3 — a legacy unparseable date is shown verbatim and preserved.
  it("preserves a legacy unparseable expiry_date verbatim", async () => {
    const legacy: Certification = { ...FULL_CERTIFICATION, expiry_date: "lifetime" };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { certifications: [legacy] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([legacy]);

    fireEvent.click(screen.getByTestId("certification-edit-0"));
    expect(screen.getByTestId("certification-expiry-date-legacy-original").textContent).toContain("lifetime");
    fireEvent.change(screen.getByTestId("certification-field-credential-id"), {
      target: { value: "AWS-999" },
    });
    fireEvent.click(screen.getByTestId("certification-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.expiry_date).toBe("lifetime");
  });

  // H1.5 — removing an entry asks for confirmation first.
  it("asks for confirmation before removing a certification, and only PATCHes on confirm", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { certifications: [] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_CERTIFICATION]);

    fireEvent.click(screen.getByTestId("certification-remove-0"));
    expect(screen.getByTestId("certification-entry-remove-dialog")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("certification-entry-remove-confirm"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  // Rule 6 — required-field validation refuses a blank/whitespace-only name
  // before any request.
  it("refuses to submit when the name is blank or whitespace-only", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([]);

    fireEvent.click(screen.getByTestId("certifications-add"));
    fireEvent.click(screen.getByTestId("certification-entry-save"));
    expect(await screen.findByTestId("certification-entry-validation-error")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("certification-field-name"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("certification-entry-save"));
    expect(await screen.findByTestId("certification-entry-validation-error")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // Rule 9 — optional empty strings are normalised to null, not "".
  it("normalises a blanked-out optional field to null rather than an empty string", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { certifications: [FULL_CERTIFICATION] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_CERTIFICATION]);

    fireEvent.click(screen.getByTestId("certification-edit-0"));
    fireEvent.change(screen.getByTestId("certification-field-issuing-organization"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByTestId("certification-entry-save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)[0];
    expect(sent.issuing_organization).toBeNull();
  });

  // H1.6 (409) — on stale_edit, no retry; reload `current`; dialog stays open; notice shown.
  it("on a 409 stale_edit, reloads from `current`, keeps the dialog open, and shows a notice", async () => {
    const current = { updated_at: "t3", profile: { certifications: [FULL_CERTIFICATION] } };
    const fetchMock = vi.fn(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_CERTIFICATION]);

    fireEvent.click(screen.getByTestId("certification-edit-0"));
    fireEvent.click(screen.getByTestId("certification-entry-save"));

    await waitFor(() => expect(screen.getByTestId("certification-entry-stale-notice")).toBeInTheDocument());
    expect(screen.getByTestId("certification-entry-dialog")).toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // H0.4 — a 200 with an unchanged vault surfaces the mismatch notice.
  it("shows a mismatch notice when the saved entry comes back unchanged", async () => {
    const unchanged = { ...FULL_CERTIFICATION };
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { certifications: [unchanged] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_CERTIFICATION]);

    fireEvent.click(screen.getByTestId("certification-edit-0"));
    fireEvent.change(screen.getByTestId("certification-field-credential-id"), {
      target: { value: "AWS-999" },
    });
    fireEvent.click(screen.getByTestId("certification-entry-save"));

    expect(await screen.findByTestId("certifications-mismatch-notice")).toBeInTheDocument();
  });

  it("shows the empty state and an add button when there are no entries", () => {
    renderEditor([]);
    expect(screen.getByText("Not provided")).toBeInTheDocument();
    expect(screen.getByTestId("certifications-add")).toBeInTheDocument();
  });

  // Adversarial finding 2026-08-26 (major): a year alone is coerced to 1 January
  // by the backend `date` field — the editor refuses year-only dates.
  it("refuses a year-only date so no January is fabricated", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_CERTIFICATION]);

    fireEvent.click(screen.getByTestId("certification-edit-0"));
    fireEvent.change(screen.getByTestId("certification-date-obtained-month"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("certification-entry-save"));

    expect(await screen.findByTestId("certification-entry-validation-error")).toHaveTextContent("month");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // Adversarial pass 2026-08-26 — F1 (blocker): a stale-conflict retry on
  // Remove must never leave the confirm dialog open on the same index.
  it("on a 409 during confirmRemove, closes the confirm dialog, reloads via onProfileUpdated, and shows a list-level stale notice", async () => {
    const current = { updated_at: "t3", profile: { certifications: [FULL_CERTIFICATION] } };
    const fetchMock = vi.fn(async () => jsonResponse(409, { detail: { error: "stale_edit", current } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { onProfileUpdated } = renderEditor([FULL_CERTIFICATION]);

    fireEvent.click(screen.getByTestId("certification-remove-0"));
    fireEvent.click(screen.getByTestId("certification-entry-remove-confirm"));

    await waitFor(() => expect(screen.getByTestId("certifications-stale-notice")).toBeInTheDocument());
    expect(screen.queryByTestId("certification-entry-remove-dialog")).not.toBeInTheDocument();
    expect(onProfileUpdated).toHaveBeenCalledWith(current);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // F1 — the removal is keyed on the entry's id, not on its position.
  it("removes the certification by id even when the list has been reordered since the confirm dialog opened", async () => {
    const other: Certification = { ...FULL_CERTIFICATION, id: "c2", name: "Other Cert" };
    const fetchMock = vi.fn(async () => jsonResponse(200, { updated_at: "t2", profile: { certifications: [other] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const { rerender } = render(withIntl(
      <CertificationsEditor
        entries={[FULL_CERTIFICATION, other]}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={vi.fn()}
      />,
    ));

    fireEvent.click(screen.getByTestId("certification-remove-1"));

    rerender(withIntl(
      <CertificationsEditor
        entries={[other, FULL_CERTIFICATION]}
        apiBase="http://api"
        profileUpdatedAt="2026-08-25T09:00:00Z"
        onProfileUpdated={vi.fn()}
      />,
    ));

    fireEvent.click(screen.getByTestId("certification-entry-remove-confirm"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(sent).toEqual([FULL_CERTIFICATION]);
  });

  it("cancelling the remove confirmation sends nothing", () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_CERTIFICATION]);

    fireEvent.click(screen.getByTestId("certification-remove-0"));
    fireEvent.click(screen.getByTestId("certification-entry-remove-cancel"));
    expect(screen.queryByTestId("certification-entry-remove-dialog")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // F1c — the confirm dialog names the entry being removed.
  it("names the entry in the remove confirmation body", () => {
    renderEditor([FULL_CERTIFICATION]);
    fireEvent.click(screen.getByTestId("certification-remove-0"));
    expect(screen.getByTestId("certification-entry-remove-dialog").textContent).toContain(
      "AWS Solutions Architect",
    );
  });

  // F2 (major) — a failed save leaves focus outside the dialog; Escape must
  // still close it via a document-level listener.
  it("closes the dialog on a document-level Escape after a failed (422) save", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(422, { detail: "name must not be blank" }));
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([FULL_CERTIFICATION]);

    fireEvent.click(screen.getByTestId("certification-edit-0"));
    fireEvent.click(screen.getByTestId("certification-entry-save"));
    await screen.findByTestId("certification-entry-dialog-error");

    fireEvent.keyDown(document.body, { key: "Escape" });

    expect(screen.queryByTestId("certification-entry-dialog")).not.toBeInTheDocument();
  });

  // F4 — two id-less legacy entries (id: "") must not collide on their React key.
  it("renders two rows for two certifications sharing an empty id, without a key warning", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const a: Certification = { ...FULL_CERTIFICATION, id: "", name: "Alpha Cert" };
    const b: Certification = { ...FULL_CERTIFICATION, id: "", name: "Beta Cert" };
    renderEditor([a, b]);

    expect(screen.getByTestId("certification-edit-0")).toBeInTheDocument();
    expect(screen.getByTestId("certification-edit-1")).toBeInTheDocument();
    const keyWarning = errorSpy.mock.calls.some((call) =>
      call.some((arg) => typeof arg === "string" && arg.includes("same key")),
    );
    expect(keyWarning).toBe(false);
    errorSpy.mockRestore();
  });
});
