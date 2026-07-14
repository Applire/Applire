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

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MobileNavDrawer } from "../MobileNavDrawer";
import { ShellUserProvider } from "../ShellUserContext";

const mockPush = vi.fn();
let mockPathname = "/dashboard";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => mockPathname,
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

function renderDrawer(open: boolean, onOpenChange = vi.fn(), userName: string | null = null) {
  return render(
    <ShellUserProvider userName={userName}>
      <MobileNavDrawer open={open} onOpenChange={onOpenChange} />
    </ShellUserProvider>
  );
}

describe("MobileNavDrawer", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockPathname = "/dashboard";
  });

  it("renders nothing in the DOM when closed", () => {
    renderDrawer(false);
    expect(screen.queryByTestId("mobile-nav-drawer")).toBeNull();
  });

  it("renders the wordmark and all six nav items when open", () => {
    renderDrawer(true);
    expect(screen.getByText("Applire")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /dashboard/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /profile/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /import/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /documents/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /settings/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /admin/i })).toBeInTheDocument();
  });

  // F9.1 (#76): the drawer must sit above other z-50 slide-over panels so it
  // is never trapped behind one — sheet.tsx primitives default to z-50.
  it("keeps the drawer content in a z-[60] stacking context", () => {
    renderDrawer(true);
    const drawer = screen.getByTestId("mobile-nav-drawer");
    expect(drawer.className).toContain("z-[60]");
  });

  it("clicking a nav item closes the drawer and navigates", () => {
    const onOpenChange = vi.fn();
    renderDrawer(true, onOpenChange);
    fireEvent.click(screen.getByRole("button", { name: /documents/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(mockPush).toHaveBeenCalledWith("/documents");
  });

  it("highlights the active nav item for the current pathname", () => {
    mockPathname = "/documents";
    renderDrawer(true);
    const btn = screen.getByRole("button", { name: /documents/i });
    expect(btn.className).toContain("bg-primary-container");
  });

  it("hides the user strip when no userName is provided", () => {
    renderDrawer(true, vi.fn(), null);
    expect(screen.queryByTestId("drawer-user-strip")).toBeNull();
  });

  it("shows the user strip with computed initials when userName is provided", () => {
    renderDrawer(true, vi.fn(), "Max Mustermann");
    expect(screen.getByTestId("drawer-user-strip")).toBeInTheDocument();
    expect(screen.getByText("MM")).toBeInTheDocument();
    expect(screen.getByText("Max Mustermann")).toBeInTheDocument();
  });
});
