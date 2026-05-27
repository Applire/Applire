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

import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { SaveScopePrompt } from "../SaveScopePrompt";

describe("SaveScopePrompt", () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders both save options", () => {
    render(withIntl(<SaveScopePrompt onConfirm={vi.fn()} onCancel={vi.fn()} />));
    expect(screen.getByTestId("save-to-profile-btn")).toBeTruthy();
    expect(screen.getByTestId("save-cv-only-btn")).toBeTruthy();
  });

  it("'Save to Profile' calls onConfirm with true", () => {
    const onConfirm = vi.fn();
    render(withIntl(<SaveScopePrompt onConfirm={onConfirm} onCancel={vi.fn()} />));
    fireEvent.click(screen.getByTestId("save-to-profile-btn"));
    expect(onConfirm).toHaveBeenCalledWith(true);
  });

  it("'Just this CV' calls onConfirm with false", () => {
    const onConfirm = vi.fn();
    render(withIntl(<SaveScopePrompt onConfirm={onConfirm} onCancel={vi.fn()} />));
    fireEvent.click(screen.getByTestId("save-cv-only-btn"));
    expect(onConfirm).toHaveBeenCalledWith(false);
  });

  it("when remember-choice is checked and CV selected, stores 'cv' in sessionStorage", () => {
    render(withIntl(<SaveScopePrompt onConfirm={vi.fn()} onCancel={vi.fn()} />));
    fireEvent.click(screen.getByTestId("remember-choice-checkbox"));
    fireEvent.click(screen.getByTestId("save-cv-only-btn"));
    expect(sessionStorage.getItem("finetune_save_scope")).toBe("cv");
  });

  it("when remember-choice is checked and profile selected, stores 'profile' in sessionStorage", () => {
    render(withIntl(<SaveScopePrompt onConfirm={vi.fn()} onCancel={vi.fn()} />));
    fireEvent.click(screen.getByTestId("remember-choice-checkbox"));
    fireEvent.click(screen.getByTestId("save-to-profile-btn"));
    expect(sessionStorage.getItem("finetune_save_scope")).toBe("profile");
  });

  it("Cancel calls onCancel", () => {
    const onCancel = vi.fn();
    render(withIntl(<SaveScopePrompt onConfirm={vi.fn()} onCancel={onCancel} />));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
