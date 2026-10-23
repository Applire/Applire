// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Hazelnut b3 WP-V (ruling V-3 = A, D-B3V6) — the import summary lists the
 * second-source bullets held back as restatements and lets the candidate add
 * one anyway.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RestatedBullets } from "../RestatedBullets";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { RestatedReceiptItem } from "@/lib/import-cv";

const row = (over: Partial<RestatedReceiptItem> = {}): RestatedReceiptItem => ({
  entity_id: "w1",
  entity_label: "Produktionsleiter @ Weberit",
  section: "work_experience",
  field: "responsibilities",
  text: "ISO-9001-Bereichsverantwortung.",
  restates: "Begleitung der jährlichen ISO-9001-Audits als Bereichsverantwortlicher.",
  ...over,
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RestatedBullets (D-B3V6)", () => {
  it("renders nothing without held-back bullets", () => {
    const { container } = render(withIntl(<RestatedBullets restated={[]} />));
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the new wording, the held bullet and the role — German copy", () => {
    render(withIntl(<RestatedBullets restated={[row()]} />, "de"));
    expect(screen.getByText("Schon in deinem Profil (anders formuliert) (1)")).toBeInTheDocument();
    expect(screen.getByTestId("restated-row")).toHaveTextContent("ISO-9001-Bereichsverantwortung.");
    expect(screen.getByTestId("restated-row")).toHaveTextContent(
      "Steht schon da: Begleitung der jährlichen ISO-9001-Audits",
    );
    expect(screen.getByText("Produktionsleiter – Weberit")).toBeInTheDocument();
    expect(screen.getByTestId("restated-apply")).toHaveTextContent("Trotzdem übernehmen");
  });

  it("'Trotzdem übernehmen' posts the bullet and shows the added state", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const onApplied = vi.fn();
    render(withIntl(<RestatedBullets restated={[row()]} onApplied={onApplied} />, "de"));
    fireEvent.click(screen.getByTestId("restated-apply"));
    await waitFor(() => expect(screen.getByTestId("restated-done")).toBeInTheDocument());
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/profile/restated/apply");
    expect(JSON.parse(init.body)).toEqual({
      entity_id: "w1",
      field: "responsibilities",
      text: "ISO-9001-Bereichsverantwortung.",
    });
    expect(onApplied).toHaveBeenCalled();
  });

  it("a failed write keeps the button and says so", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 409 })));
    render(withIntl(<RestatedBullets restated={[row()]} />, "de"));
    fireEvent.click(screen.getByTestId("restated-apply"));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByTestId("restated-apply")).toBeInTheDocument();
  });

  it("folds after five rows", () => {
    const many = Array.from({ length: 7 }, (_, i) => row({ text: `Punkt ${i}.` }));
    render(withIntl(<RestatedBullets restated={many} />, "en"));
    expect(screen.getAllByTestId("restated-row")).toHaveLength(5);
    fireEvent.click(screen.getByTestId("restated-show-all"));
    expect(screen.getAllByTestId("restated-row")).toHaveLength(7);
  });
});
