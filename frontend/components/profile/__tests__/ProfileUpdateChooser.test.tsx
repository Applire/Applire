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

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ProfileUpdateChooser } from "../ProfileUpdateChooser";
import { withIntl } from "@/lib/test-utils/with-intl";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/profile/upload",
}));

// The first-run screen and the import banner have their own tests.
vi.mock("@/components/onboarding/FirstRunScreen", () => ({
  FirstRunScreen: () => <div data-testid="first-run" />,
}));
vi.mock("@/components/dashboard/ImportInProgressBanner", () => ({
  ImportInProgressBanner: () => null,
}));

const realFetch = global.fetch;
afterEach(() => {
  global.fetch = realFetch;
});

function mockExists(exists: boolean | "unreadable") {
  global.fetch = vi.fn((url: string) => {
    if (url.includes("/api/profile/exists")) {
      return exists === "unreadable"
        ? Promise.resolve({ ok: false, json: async () => ({}) })
        : Promise.resolve({ ok: true, json: async () => ({ exists }) });
    }
    return Promise.resolve({ ok: false, json: async () => ({}) });
  }) as unknown as typeof fetch;
}

describe("ProfileUpdateChooser", () => {
  it("renders both action cards with i18n labels (en)", async () => {
    mockExists(true);
    render(withIntl(<ProfileUpdateChooser />, "en"));
    await waitFor(() => screen.getByTestId("profile-update-chooser"));
    expect(screen.getByRole("link", { name: /Upload a new CV/i })).toHaveAttribute(
      "href",
      "/profile/upload?action=upload"
    );
    expect(screen.getByRole("link", { name: /I started a new role/i })).toHaveAttribute(
      "href",
      "/profile/upload?action=add-role&source=manual"
    );
  });

  it("renders German labels under the de locale", async () => {
    mockExists(true);
    render(withIntl(<ProfileUpdateChooser />, "de"));
    await waitFor(() => screen.getByTestId("profile-update-chooser"));
    expect(screen.getByText(/Neuen Lebenslauf hochladen/)).toBeInTheDocument();
    expect(screen.getByText(/Ich habe eine neue Stelle angetreten/)).toBeInTheDocument();
  });

  // Hazelnut b3 A-11 — first contact through the sidebar entry.
  it("a person without a profile gets the first-run screen, never 'Was hat sich geändert?'", async () => {
    mockExists(false);
    render(withIntl(<ProfileUpdateChooser />, "de"));
    await waitFor(() => screen.getByTestId("profile-update-first-run"));
    expect(screen.getByTestId("first-run")).toBeInTheDocument();
    expect(screen.queryByText(/Was hat sich geändert/)).toBeNull();
    expect(screen.queryByText(/neue Stelle angetreten/)).toBeNull();
  });

  it("while the check runs, neither frame flashes", () => {
    global.fetch = vi.fn(() => new Promise(() => {})) as unknown as typeof fetch;
    render(withIntl(<ProfileUpdateChooser />, "de"));
    expect(screen.queryByTestId("profile-update-chooser")).toBeNull();
    expect(screen.queryByTestId("first-run")).toBeNull();
  });

  it("an unreadable check falls back to the chooser", async () => {
    mockExists("unreadable");
    render(withIntl(<ProfileUpdateChooser />, "de"));
    await waitFor(() => screen.getByTestId("profile-update-chooser"));
    expect(screen.queryByTestId("first-run")).toBeNull();
  });
});
