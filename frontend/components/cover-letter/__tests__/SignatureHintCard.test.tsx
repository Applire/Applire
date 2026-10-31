// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// ADR-099 cl. 9 / RULING C-1 (3): the letter's signature card — two answers.
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { withIntl } from "@/lib/test-utils/with-intl";
import { SignatureHintCard } from "../SignatureHintCard";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

describe("SignatureHintCard", () => {
  it("offers „Im Profil hinzufügen“ (to the profile) and „Ohne Unterschrift“ (remembered)", () => {
    const onWithout = vi.fn();
    render(withIntl(<SignatureHintCard onWithout={onWithout} saving={false} failed={false} />, "de"));
    expect(screen.getByTestId("review-signature-card").textContent).toContain("Keine Unterschrift hinterlegt");
    fireEvent.click(screen.getByTestId("review-signature-add"));
    expect(push).toHaveBeenCalledWith("/profile");
    fireEvent.click(screen.getByTestId("review-signature-without"));
    expect(onWithout).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("review-signature-error")).toBeNull();
  });

  it("says when the choice could not be saved", () => {
    render(withIntl(<SignatureHintCard onWithout={vi.fn()} saving={false} failed />, "en"));
    expect(screen.getByTestId("review-signature-error")).toBeTruthy();
  });
});
