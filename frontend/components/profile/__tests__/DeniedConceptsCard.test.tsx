// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// G-5 (ADR-059 amended 2026-10-24) — My Profile lists every denial with
// "Zurücknehmen"; the withdrawal goes through a confirm dialog quoting the
// candidate's own statement, never one click.
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DeniedConceptsCard } from "../DeniedConceptsCard";
import { withIntl } from "@/lib/test-utils/with-intl";

const DENIED = [
  { concept: "SAP PP", statement: "SAP PP habe ich nie selbst bedient.", source: "interview" as const, date: "2026-10-12" },
  { concept: "IFS Food", statement: "Mit IFS Food habe ich selbst nicht gearbeitet.", source: "testimony" as const, date: "2026-10-18" },
];
const WITHDRAWN = [{ ...DENIED[0], concept: "Schichtplanung", withdrawn_at: "2026-10-20" }];

afterEach(() => vi.unstubAllGlobals());

describe("DeniedConceptsCard (G-5)", () => {
  it("lists each denial with the candidate's words, source and date, in German", () => {
    render(withIntl(<DeniedConceptsCard denied={DENIED} withdrawn={WITHDRAWN} onWithdrawn={vi.fn()} />, "de"));
    const rows = screen.getAllByTestId("denied-concept");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("„SAP PP habe ich nie selbst bedient.“");
    expect(rows[0].textContent).toContain("Aus deinem Gespräch am 12.10.2026");
    expect(rows[1].textContent).toContain("Aus einer Ergänzung zu deinem Profil am 18.10.2026");
    expect(screen.getByTestId("withdrawn-denials").textContent).toContain("Schichtplanung, am 20.10.2026");
  });

  it("asks before it withdraws, then posts the concept and refreshes", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal("fetch", fetchMock);
    const onWithdrawn = vi.fn();
    render(withIntl(<DeniedConceptsCard denied={DENIED} withdrawn={[]} onWithdrawn={onWithdrawn} />, "de"));

    fireEvent.click(screen.getAllByTestId("denied-concept-withdraw")[0]);
    expect(fetchMock).not.toHaveBeenCalled();
    const dialog = screen.getByTestId("withdraw-denial-dialog");
    expect(dialog.textContent).toContain("„SAP PP“ zurücknehmen?");
    expect(dialog.textContent).toContain("SAP PP habe ich nie selbst bedient.");

    fireEvent.click(screen.getByTestId("withdraw-denial-confirm"));
    await waitFor(() => expect(onWithdrawn).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/profile\/denials\/withdraw$/);
    expect(JSON.parse(init.body)).toEqual({ concept: "SAP PP" });
    expect(screen.queryByTestId("withdraw-denial-dialog")).toBeNull();
  });

  it("cancel writes nothing", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(withIntl(<DeniedConceptsCard denied={DENIED} withdrawn={[]} onWithdrawn={vi.fn()} />, "en"));
    fireEvent.click(screen.getAllByTestId("denied-concept-withdraw")[1]);
    fireEvent.click(screen.getByText("Cancel"));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("withdraw-denial-dialog")).toBeNull();
  });

  it("keeps the dialog open and says so when the act failed", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));
    const onWithdrawn = vi.fn();
    render(withIntl(<DeniedConceptsCard denied={DENIED} withdrawn={[]} onWithdrawn={onWithdrawn} />, "en"));
    fireEvent.click(screen.getAllByTestId("denied-concept-withdraw")[0]);
    fireEvent.click(screen.getByTestId("withdraw-denial-confirm"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(onWithdrawn).not.toHaveBeenCalled();
  });

  it("renders nothing without any denial", () => {
    const { container } = render(withIntl(<DeniedConceptsCard denied={[]} withdrawn={[]} onWithdrawn={vi.fn()} />));
    expect(container.textContent).toBe("");
  });
});
