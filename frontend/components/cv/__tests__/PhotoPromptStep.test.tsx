// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { withIntl } from "@/lib/test-utils/with-intl";

const getSettings = vi.fn();
const dismissExplainer = vi.fn().mockResolvedValue(undefined);
vi.mock("@/lib/api/settings", () => ({
  getSettings: () => getSettings(),
  dismissExplainer: (id: string) => dismissExplainer(id),
}));
vi.mock("@/components/profile/PhotoManager", () => ({
  PhotoManager: () => <div data-testid="photo-manager-stub" />,
}));

import { PhotoPromptStep, PHOTO_PROMPT_EXPLAINER_ID } from "../PhotoPromptStep";

describe("PhotoPromptStep (#789 — the remembered 'no photo')", () => {
  beforeEach(() => {
    getSettings.mockReset();
    dismissExplainer.mockClear();
  });

  it("a remembered 'no photo' skips the step without painting it", async () => {
    getSettings.mockResolvedValue({ dismissed_explainers: [PHOTO_PROMPT_EXPLAINER_ID] });
    const onContinue = vi.fn();
    render(withIntl(<PhotoPromptStep onContinue={onContinue} />));
    expect(screen.queryByTestId("photo-prompt-skip")).toBeNull();
    await waitFor(() => expect(onContinue).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("photo-prompt-skip")).toBeNull();
  });

  it("asks when nothing is remembered; 'Continue without a photo' remembers it", async () => {
    getSettings.mockResolvedValue({ dismissed_explainers: [] });
    const onContinue = vi.fn();
    render(withIntl(<PhotoPromptStep onContinue={onContinue} />));
    fireEvent.click(await screen.findByTestId("photo-prompt-skip"));
    expect(dismissExplainer).toHaveBeenCalledWith("cv_photo_prompt");
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("a settings failure asks (never a gate)", async () => {
    getSettings.mockRejectedValue(new Error("down"));
    render(withIntl(<PhotoPromptStep onContinue={vi.fn()} />));
    expect(await screen.findByTestId("photo-prompt-upload")).toBeTruthy();
  });

  it("upload is the primary choice and never claims a stored photo; skip is secondary", async () => {
    getSettings.mockResolvedValue({ dismissed_explainers: [] });
    render(withIntl(<PhotoPromptStep onContinue={vi.fn()} />));
    const upload = await screen.findByTestId("photo-prompt-upload");
    expect(upload.className).toContain("bg-primary");
    expect(screen.getByTestId("photo-prompt-skip").className).not.toContain("bg-primary");
    expect(upload.textContent).not.toContain("Saved to your profile");
    // No emoji glyphs (Material Symbols identifiers instead).
    expect(document.body.textContent ?? "").not.toMatch(/[\u{1F4F7}⏭]/u);
  });

  it("in the upload view, 'Continue without a photo' is the secondary button and remembers the choice", async () => {
    getSettings.mockResolvedValue({ dismissed_explainers: [] });
    const onContinue = vi.fn();
    render(withIntl(<PhotoPromptStep onContinue={onContinue} />));
    fireEvent.click(await screen.findByTestId("photo-prompt-upload"));
    const skip = screen.getByTestId("photo-upload-skip");
    expect(skip.className).not.toContain("bg-teal ");
    fireEvent.click(skip);
    expect(dismissExplainer).toHaveBeenCalledWith("cv_photo_prompt");
    expect(onContinue).toHaveBeenCalled();
  });
});
