// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeAll } from "vitest";

import { withIntl } from "@/lib/test-utils/with-intl";

vi.mock("@/components/profile/PhotoCropDialog", () => ({
  FramedPhoto: () => null,
  PhotoCropDialog: () => <div data-testid="crop-dialog-stub" />,
}));

import { PhotoManager } from "../PhotoManager";

beforeAll(() => {
  // jsdom has no object URLs.
  (URL as unknown as { createObjectURL: () => string }).createObjectURL = () => "blob:x";
  (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = () => {};
});

function pick(file: File) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

describe("PhotoManager (#789 — consent order, the held file)", () => {
  it("consent comes BEFORE the drop zone", () => {
    render(withIntl(<PhotoManager />));
    const consent = screen.getByTestId("photo-consent");
    const dropzone = screen.getByTestId("photo-dropzone");
    expect(consent.compareDocumentPosition(dropzone) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("a file picked before consent is held, not discarded — ticking consent continues with it", () => {
    render(withIntl(<PhotoManager />));
    pick(new File(["x"], "portrait.jpg", { type: "image/jpeg" }));
    expect(screen.getByTestId("photo-held").textContent).toContain("portrait.jpg");
    expect(screen.queryByText(/Please tick the consent checkbox before uploading/)).toBeNull();
    expect(screen.queryByTestId("crop-dialog-stub")).toBeNull();

    fireEvent.click(screen.getByTestId("photo-consent"));
    expect(screen.getByTestId("crop-dialog-stub")).toBeTruthy();
    expect(screen.queryByTestId("photo-held")).toBeNull();
  });

  it("with consent given first, a pick opens the framing straight away", () => {
    render(withIntl(<PhotoManager />));
    fireEvent.click(screen.getByTestId("photo-consent"));
    pick(new File(["x"], "portrait.jpg", { type: "image/jpeg" }));
    expect(screen.getByTestId("crop-dialog-stub")).toBeTruthy();
  });
});
