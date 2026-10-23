// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// Ruling B3-12 (founder 2026-10-23): a limit recorded after the CV gets its own
// hint, and it wins over growth. Rendered with the REAL de/en catalogs.

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { withIntl } from "@/lib/test-utils/with-intl";
import { StaleCvBanner } from "../StaleCvBanner";

function renderBanner(locale: "de" | "en", reason: "profile_grew" | "new_limit") {
  return render(
    withIntl(
      <StaleCvBanner
        gained={[{ section: "skills", count: 2 }]}
        reason={reason}
        canRetailor
        retailoring={false}
        onRetailor={vi.fn()}
        onDismiss={vi.fn()}
      />,
      locale,
    ),
  );
}

describe("StaleCvBanner — the limit hint (B3-12)", () => {
  it("DE: its own title and sentence, and the limit wins over the gained list", () => {
    renderBanner("de", "new_limit");
    const banner = screen.getByTestId("stale-cv-banner");
    expect(banner.textContent).toContain("Neue Grenze in deinem Profil");
    expect(screen.getByTestId("stale-cv-body").textContent).toBe(
      "Du hast eine Grenze angegeben, die dieser Lebenslauf noch nicht berücksichtigt. Jetzt neu zuschneiden?",
    );
    expect(banner.textContent).not.toContain("gewachsen");
    expect(banner.textContent).not.toContain("Fähigkeiten +2");
  });

  it("EN: its own title and sentence", () => {
    renderBanner("en", "new_limit");
    expect(screen.getByTestId("stale-cv-banner").textContent).toContain("New limit in your profile");
    expect(screen.getByTestId("stale-cv-body").textContent).toBe(
      "You recorded a limit this CV does not reflect yet. Tailor it again?",
    );
  });

  it("growth keeps its copy", () => {
    renderBanner("de", "profile_grew");
    expect(screen.getByTestId("stale-cv-banner").textContent).toContain("Dein Profil ist gewachsen");
    expect(screen.getByTestId("stale-cv-body").textContent).toContain("Fähigkeiten +2");
  });
});
