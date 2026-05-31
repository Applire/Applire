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

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ProfileUpdateChooser } from "../ProfileUpdateChooser";
import { withIntl } from "@/lib/test-utils/with-intl";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

describe("ProfileUpdateChooser", () => {
  it("renders both action cards with i18n labels (en)", () => {
    render(withIntl(<ProfileUpdateChooser />, "en"));
    expect(screen.getByRole("link", { name: /Upload a new CV/i })).toHaveAttribute(
      "href",
      "/profile/upload?action=upload"
    );
    expect(screen.getByRole("link", { name: /I started a new role/i })).toHaveAttribute(
      "href",
      "/profile/upload?action=add-role&source=manual"
    );
  });

  it("renders German labels under the de locale", () => {
    render(withIntl(<ProfileUpdateChooser />, "de"));
    expect(screen.getByText(/Neuen Lebenslauf hochladen/)).toBeInTheDocument();
    expect(screen.getByText(/Ich habe eine neue Stelle angetreten/)).toBeInTheDocument();
  });
});
