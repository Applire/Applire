// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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

/** #359 — the signature card: upload, preview, delete, and the two toggles. */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { SignatureManager } from "../SignatureManager";

const SETTINGS = { signature_in_letter: true, signature_in_cv: false };

function jsonOk(body: unknown) {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as Response;
}
function notFound() {
  return { ok: false, status: 404, json: () => Promise.resolve({}) } as Response;
}

/** Route by URL+method so a test never depends on call ORDER. */
function mockApi(handlers: Record<string, (init?: RequestInit) => Response>) {
  return vi.spyOn(global, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    for (const [key, fn] of Object.entries(handlers)) {
      const [m, u] = key.split(" ");
      if (m === method && url.endsWith(u)) return Promise.resolve(fn(init));
    }
    return Promise.resolve(notFound());
  });
}

beforeEach(() => {
  // jsdom has no object-URL implementation.
  global.URL.createObjectURL = vi.fn(() => "blob:signature");
  global.URL.revokeObjectURL = vi.fn();
});
afterEach(() => vi.restoreAllMocks());

describe("SignatureManager", () => {
  it("shows the dropzone when nothing is on file (404 is not an error)", async () => {
    mockApi({ "GET /api/settings": () => jsonOk(SETTINGS) });
    render(withIntl(<SignatureManager />));
    await waitFor(() => expect(screen.getByTestId("signature-toggle-letter")).toBeTruthy());
    expect(screen.getByTestId("signature-dropzone")).toBeTruthy();
    expect(screen.queryByTestId("signature-preview")).toBeNull();
    // A 404 from the image endpoint is the ordinary "none yet" answer.
    expect(screen.queryByTestId("signature-error")).toBeNull();
  });

  it("shows the preview and the two actions when one IS on file", async () => {
    mockApi({
      "GET /api/profile/signature": () =>
        ({ ok: true, status: 200, blob: () => Promise.resolve(new Blob(["x"])) }) as unknown as Response,
      "GET /api/settings": () => jsonOk(SETTINGS),
    });
    render(withIntl(<SignatureManager />));
    await waitFor(() => expect(screen.getByTestId("signature-preview")).toBeTruthy());
    expect(screen.getByTestId("signature-replace")).toBeTruthy();
    expect(screen.getByTestId("signature-delete")).toBeTruthy();
    expect(screen.queryByTestId("signature-dropzone")).toBeNull();
  });

  it("carries the founder's defaults: letter ON, CV OFF", async () => {
    mockApi({ "GET /api/settings": () => jsonOk(SETTINGS) });
    render(withIntl(<SignatureManager />));
    await waitFor(() => expect(screen.getByTestId("signature-toggle-letter")).toBeTruthy());
    expect((screen.getByTestId("signature-toggle-letter") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("signature-toggle-cv") as HTMLInputElement).checked).toBe(false);
  });

  it("PATCHes one field when a toggle is flipped", async () => {
    const patched: string[] = [];
    mockApi({
      "GET /api/settings": () => jsonOk(SETTINGS),
      "PATCH /api/settings": (init) => {
        patched.push(String(init?.body));
        return jsonOk({ ...SETTINGS, signature_in_cv: true });
      },
    });
    render(withIntl(<SignatureManager />));
    await waitFor(() => expect(screen.getByTestId("signature-toggle-cv")).toBeTruthy());
    fireEvent.click(screen.getByTestId("signature-toggle-cv"));
    await waitFor(() => expect(patched.length).toBe(1));
    expect(JSON.parse(patched[0])).toEqual({ signature_in_cv: true });
  });

  it("reverts the switch and says so when the PATCH fails", async () => {
    mockApi({
      "GET /api/settings": () => jsonOk(SETTINGS),
      "PATCH /api/settings": () => ({ ok: false, status: 500, json: () => Promise.resolve({}) }) as Response,
    });
    render(withIntl(<SignatureManager />));
    await waitFor(() => expect(screen.getByTestId("signature-toggle-cv")).toBeTruthy());
    fireEvent.click(screen.getByTestId("signature-toggle-cv"));
    await waitFor(() => expect(screen.getByTestId("signature-error")).toBeTruthy());
    // A switch that moved and silently did not save is worse than one that
    // moves back.
    expect((screen.getByTestId("signature-toggle-cv") as HTMLInputElement).checked).toBe(false);
  });

  it("surfaces the backend's own rejection message on upload", async () => {
    mockApi({
      "GET /api/settings": () => jsonOk(SETTINGS),
      "POST /api/profile/signature": () =>
        ({
          ok: false,
          status: 400,
          json: () => Promise.resolve({ detail: "Signature exceeds the 2 MB limit." }),
        }) as Response,
    });
    render(withIntl(<SignatureManager />));
    await waitFor(() => expect(screen.getByTestId("signature-dropzone")).toBeTruthy());
    const input = screen.getByTestId("signature-file-input") as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["x"], "sig.png", { type: "image/png" })] },
    });
    await waitFor(() =>
      expect(screen.getByTestId("signature-error").textContent).toContain("2 MB"),
    );
  });

  it("uploads, then shows the preview and reports the change", async () => {
    const onSignatureChange = vi.fn();
    mockApi({
      "GET /api/settings": () => jsonOk(SETTINGS),
      "POST /api/profile/signature": () => jsonOk({ signature_url: "/uploads/signature.png" }),
    });
    render(withIntl(<SignatureManager onSignatureChange={onSignatureChange} />));
    await waitFor(() => expect(screen.getByTestId("signature-dropzone")).toBeTruthy());
    fireEvent.change(screen.getByTestId("signature-file-input"), {
      target: { files: [new File(["x"], "sig.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(screen.getByTestId("signature-preview")).toBeTruthy());
    expect(onSignatureChange).toHaveBeenCalledWith(true);
  });

  it("deletes and returns to the dropzone", async () => {
    const onSignatureChange = vi.fn();
    mockApi({
      "GET /api/profile/signature": () =>
        ({ ok: true, status: 200, blob: () => Promise.resolve(new Blob(["x"])) }) as unknown as Response,
      "GET /api/settings": () => jsonOk(SETTINGS),
      "DELETE /api/profile/signature": () => ({ ok: true, status: 204, json: () => Promise.resolve({}) }) as Response,
    });
    render(withIntl(<SignatureManager onSignatureChange={onSignatureChange} />));
    await waitFor(() => expect(screen.getByTestId("signature-delete")).toBeTruthy());
    fireEvent.click(screen.getByTestId("signature-delete"));
    await waitFor(() => expect(screen.getByTestId("signature-dropzone")).toBeTruthy());
    expect(onSignatureChange).toHaveBeenCalledWith(false);
  });

  it("asks for NO consent — the photo's Art. 9 gate has no twin here", async () => {
    mockApi({ "GET /api/settings": () => jsonOk(SETTINGS) });
    const { container } = render(withIntl(<SignatureManager />, "de"));
    await waitFor(() => expect(screen.getByTestId("signature-dropzone")).toBeTruthy());
    expect(container.textContent).not.toMatch(/Einwilligung|einverstanden|Zustimmung/i);
    expect(container.querySelector("#photo-consent")).toBeNull();
  });

  it("renders real German, not the English string", async () => {
    mockApi({ "GET /api/settings": () => jsonOk(SETTINGS) });
    render(withIntl(<SignatureManager />, "de"));
    await waitFor(() => expect(screen.getByText("Unterschrift")).toBeTruthy());
    expect(screen.getByTestId("signature-dropzone").textContent).toContain("Unterschrift hochladen");
  });
});
