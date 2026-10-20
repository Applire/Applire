// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import {
  acceptWithoutImages,
  IMAGE_UPLOAD_EXTENSIONS,
  useImportCapabilities,
} from "@/lib/import-capabilities";
import { CV_UPLOAD_ACCEPT } from "@/components/ui/dropzone";

function mockCapabilities(body: unknown, ok = true) {
  const fn = vi.fn(async () => ({ ok, status: ok ? 200 : 500, json: async () => body }) as Response);
  global.fetch = fn as unknown as typeof fetch;
  return fn;
}

afterEach(() => vi.restoreAllMocks());

describe("acceptWithoutImages (#789 L12)", () => {
  it("drops every image extension and keeps the rest of the picker list", () => {
    const out = acceptWithoutImages(CV_UPLOAD_ACCEPT).split(",");
    for (const ext of IMAGE_UPLOAD_EXTENSIONS) expect(out).not.toContain(ext);
    expect(out).toEqual([".pdf", ".docx", ".doc", ".txt", ".md"]);
  });

  it("covers every image extension CV_UPLOAD_ACCEPT offers (no image left behind)", () => {
    const offered = CV_UPLOAD_ACCEPT.split(",");
    const nonImage = new Set([".pdf", ".docx", ".doc", ".txt", ".md"]);
    for (const ext of offered.filter((e) => !nonImage.has(e))) {
      expect(IMAGE_UPLOAD_EXTENSIONS, `${ext} is an image extension`).toContain(ext);
    }
  });

  it("leaves a list without images, and a .zip addition, untouched", () => {
    expect(acceptWithoutImages(".pdf,.zip")).toBe(".pdf,.zip");
  });
});

describe("useImportCapabilities (#789 L12)", () => {
  it("offers images until the instance says otherwise", () => {
    mockCapabilities({ image_import: false });
    const { result } = renderHook(() => useImportCapabilities());
    expect(result.current.imageImport).toBe(true);
  });

  it("turns images off when the backend reports image_import false", async () => {
    const fetchMock = mockCapabilities({ image_import: false });
    const { result } = renderHook(() => useImportCapabilities());
    await waitFor(() => expect(result.current.imageImport).toBe(false));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/profile/import-capabilities");
  });

  it("keeps images on when the backend reports image_import true", async () => {
    const fetchMock = mockCapabilities({ image_import: true });
    const { result } = renderHook(() => useImportCapabilities());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(result.current.imageImport).toBe(true);
  });

  it.each([
    ["a failed request", () => mockCapabilities({}, false)],
    ["a garbled body", () => mockCapabilities([])],
    ["a non-boolean flag", () => mockCapabilities({ image_import: "no" })],
    [
      "a network error",
      () => {
        global.fetch = vi.fn(async () => {
          throw new Error("offline");
        }) as unknown as typeof fetch;
        return global.fetch as unknown as ReturnType<typeof vi.fn>;
      },
    ],
  ])("never hides images on %s (the failure message names the cause anyway)", async (_n, setup) => {
    const fetchMock = setup();
    const { result } = renderHook(() => useImportCapabilities());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(result.current.imageImport).toBe(true);
  });

  it("does not call the API while disabled (an unauthenticated first screen)", () => {
    const fetchMock = mockCapabilities({ image_import: false });
    renderHook(() => useImportCapabilities(false));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
