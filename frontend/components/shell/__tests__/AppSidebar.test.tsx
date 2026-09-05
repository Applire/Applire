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
import { AppSidebar } from "../AppSidebar";

const mockPush = vi.fn();
let mockPathname = "/dashboard";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => mockPathname,
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

describe("AppSidebar", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockPathname = "/dashboard";
  });

  it("renders Applire logo image", () => {
    render(<AppSidebar />);
    expect(screen.getByRole("img", { name: /appName/i })).toBeInTheDocument();
  });

  it("renders Applire brand name", () => {
    render(<AppSidebar />);
    expect(screen.getByText("Applire")).toBeInTheDocument();
  });

  it("renders all six nav items", () => {
    render(<AppSidebar />);
    expect(screen.getByRole("button", { name: /dashboard/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /profile/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /import/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /documents/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /settings/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /admin/i })).toBeInTheDocument();
  });

  it("highlights Dashboard when pathname is /dashboard", () => {
    mockPathname = "/dashboard";
    render(<AppSidebar />);
    const btn = screen.getByRole("button", { name: /dashboard/i });
    expect(btn.className).toContain("bg-primary-container");
  });

  it("highlights import nav when pathname is /profile/upload", () => {
    mockPathname = "/profile/upload";
    render(<AppSidebar />);
    const btn = screen.getByRole("button", { name: /import/i });
    expect(btn.className).toContain("bg-primary-container");
  });

  it("does not highlight profile nav when pathname is /profile/upload", () => {
    mockPathname = "/profile/upload";
    render(<AppSidebar />);
    const btn = screen.getByRole("button", { name: /profile/i });
    expect(btn.className).not.toContain("bg-primary-container");
  });

  it("highlights Documents when pathname is /documents", () => {
    mockPathname = "/documents";
    render(<AppSidebar />);
    const btn = screen.getByRole("button", { name: /documents/i });
    expect(btn.className).toContain("bg-primary-container");
  });

  it("does not highlight Dashboard when on documents path", () => {
    mockPathname = "/documents";
    render(<AppSidebar />);
    const btn = screen.getByRole("button", { name: /dashboard/i });
    expect(btn.className).not.toContain("bg-primary-container");
  });

  it("clicking Documents navigates to /documents", () => {
    render(<AppSidebar />);
    fireEvent.click(screen.getByRole("button", { name: /documents/i }));
    expect(mockPush).toHaveBeenCalledWith("/documents");
  });

  it("clicking Profile navigates to /profile", () => {
    render(<AppSidebar />);
    fireEvent.click(screen.getByRole("button", { name: /profile/i }));
    expect(mockPush).toHaveBeenCalledWith("/profile");
  });

  it("clicking import nav navigates to /profile/upload", () => {
    render(<AppSidebar />);
    fireEvent.click(screen.getByRole("button", { name: /import/i }));
    expect(mockPush).toHaveBeenCalledWith("/profile/upload");
  });

  it("computes initials from userName 'Max Mustermann' → 'MM'", () => {
    render(<AppSidebar userName="Max Mustermann" />);
    expect(screen.getByText("MM")).toBeInTheDocument();
  });

  it("shows single initial for single-word name", () => {
    render(<AppSidebar userName="Felix" />);
    expect(screen.getByText("F")).toBeInTheDocument();
  });

  it("hides the user strip when no userName is provided", () => {
    render(<AppSidebar />);
    expect(screen.queryByTestId("sidebar-user-strip")).toBeNull();
  });

  it("displays userName in the user strip", () => {
    render(<AppSidebar userName="Tobias Rosenbaum" />);
    expect(screen.getByText("Tobias Rosenbaum")).toBeInTheDocument();
  });

  it("hides the user strip when userName is null", () => {
    render(<AppSidebar userName={null} />);
    expect(screen.queryByTestId("sidebar-user-strip")).toBeNull();
  });

  it("renders 2-letter initials when full name provided", () => {
    render(<AppSidebar userName="Marcus Brandt" />);
    expect(screen.getByText("MB")).toBeInTheDocument();
  });

  it("exposes an Admin nav entry", () => {
    render(<AppSidebar />);
    // The mock returns the key as-is, so t("admin") → "admin"
    expect(screen.getByRole("button", { name: /admin/i })).toBeInTheDocument();
  });

  it("does not render a help button", () => {
    render(<AppSidebar />);
    expect(screen.queryByRole("button", { name: /help/i })).not.toBeInTheDocument();
  });

  it("renders a version string in the footer", () => {
    render(<AppSidebar />);
    // NEXT_PUBLIC_APP_VERSION is undefined in test env — component falls back to empty string
    // We just check the footer element exists with the right test-id
    expect(screen.getByTestId("sidebar-version")).toBeInTheDocument();
  });

  it("computes correct initials when userName has a double space", () => {
    render(<AppSidebar userName="Tobias  Rosenbaum" />);
    expect(screen.getByText("TR")).toBeInTheDocument();
  });

  // F9.1 (#76): an open slide-over drawer uses z-50; the sidebar must sit above it
  // (its own stacking context) so navigation is never trapped behind the overlay.
  it("keeps the nav in a stacking context above slide-over drawers", () => {
    render(<AppSidebar />);
    const aside = screen.getByTestId("app-sidebar");
    expect(aside.className).toContain("relative");
    expect(aside.className).toContain("z-[60]");
  });

  // US223: below md the fixed 240px sidebar is hidden — mobile gets the
  // hamburger + MobileNavDrawer (AppTopbar) instead.
  it("is hidden below md and reappears as a flex column at md and up", () => {
    render(<AppSidebar />);
    const aside = screen.getByTestId("app-sidebar");
    expect(aside.className).toContain("hidden");
    expect(aside.className).toContain("md:flex");
  });

  // E058/US299 (ADR-081 cl. 1): on the two document result routes the app nav
  // collapses to a 56 px icon rail — expandable, NEVER removed. This is the
  // first time the shell's shape depends on the ROUTE rather than only on the
  // breakpoint (arc42 §5.3.21), so the route match is pinned in both
  // directions: it fires on the document routes and on nothing else.
  describe("document-route icon rail (E058/US299)", () => {
    it("renders the rail on the CV result route", () => {
      mockPathname = "/flow/abc-123/cv";
      render(<AppSidebar />);
      const aside = screen.getByTestId("app-sidebar");
      expect(aside.getAttribute("data-variant")).toBe("rail");
      expect(aside.className).toContain("w-14");
    });

    it("renders the rail on the cover-letter result route", () => {
      mockPathname = "/flow/abc-123/cover-letter";
      render(<AppSidebar />);
      expect(screen.getByTestId("app-sidebar").getAttribute("data-variant")).toBe("rail");
    });

    it("keeps the 240 px sidebar on every other route", () => {
      for (const path of ["/dashboard", "/documents", "/flow/abc-123/gaps", "/flow/abc-123"]) {
        mockPathname = path;
        const { unmount } = render(<AppSidebar />);
        expect(screen.getByTestId("app-sidebar").getAttribute("data-variant")).toBe("full");
        unmount();
      }
    });

    it("keeps every nav destination reachable on the rail — a collapse, not a removal", () => {
      mockPathname = "/flow/abc-123/cv";
      render(<AppSidebar />);
      for (const name of [/dashboard/i, /profile/i, /import/i, /documents/i, /settings/i, /admin/i]) {
        expect(screen.getByRole("button", { name })).toBeInTheDocument();
      }
    });

    it("still navigates from the rail", () => {
      mockPathname = "/flow/abc-123/cv";
      render(<AppSidebar />);
      fireEvent.click(screen.getByRole("button", { name: /documents/i }));
      expect(mockPush).toHaveBeenCalledWith("/documents");
    });

    it("expands back to the full sidebar through a NAMED control, and collapses again", () => {
      mockPathname = "/flow/abc-123/cv";
      render(<AppSidebar />);
      const expand = screen.getByTestId("app-sidebar-rail-expand");
      expect(expand.getAttribute("aria-expanded")).toBe("false");
      fireEvent.click(expand);
      expect(screen.getByTestId("app-sidebar").getAttribute("data-variant")).toBe("full");
      fireEvent.click(screen.getByTestId("app-sidebar-rail-collapse"));
      expect(screen.getByTestId("app-sidebar").getAttribute("data-variant")).toBe("rail");
    });

    it("keeps the rail above slide-over drawers and hidden below md, exactly like the full sidebar", () => {
      mockPathname = "/flow/abc-123/cv";
      render(<AppSidebar />);
      const aside = screen.getByTestId("app-sidebar");
      expect(aside.className).toContain("z-[60]");
      expect(aside.className).toContain("hidden");
      expect(aside.className).toContain("md:flex");
    });
  });
});
