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
import { NextIntlClientProvider } from "next-intl";
import messages from "@/messages/de.json";
import { AppTopbar } from "@/components/shell/AppTopbar";

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));

function withIntl(c: React.ReactNode) {
  return (
    <NextIntlClientProvider locale="de" messages={messages}>{c}</NextIntlClientProvider>
  );
}

describe("AppTopbar", () => {
  it("section mode renders a single h1 with the section title", () => {
    render(withIntl(<AppTopbar mode="section" titleKey="shell.dashboard" />));
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toBe(messages.shell.dashboard);
  });

  it("detail mode renders a back link + page title", () => {
    render(withIntl(
      <AppTopbar mode="detail" backHref="/dashboard" backLabelKey="shell.dashboard" pageTitle="Senior QA Manager" />
    ));
    expect(screen.getByRole("link", { name: /← .*Dashboard/i })).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Senior QA Manager");
  });

  it("flow mode renders the wizard stepper instead of a title", () => {
    render(withIntl(
      <AppTopbar mode="flow" steps={[
        { key: "cv_import",     labelKey: "flow.stepProfile",   state: "done"    },
        { key: "gap_analysis",  labelKey: "flow.stepGaps",      state: "active"  },
        { key: "interview",     labelKey: "flow.stepInterview", state: "pending" },
        { key: "cv_generation", labelKey: "flow.stepCV",        state: "pending" },
      ]} />
    ));
    expect(screen.getByText(messages.flow.stepProfile)).toBeInTheDocument();
    expect(screen.getByText(messages.flow.stepGaps)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });
});
