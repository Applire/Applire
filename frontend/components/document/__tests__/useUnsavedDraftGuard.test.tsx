// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/** Adversarial finding 11 — the unsaved-draft guard's own contract. */
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { internalNavTarget, useUnsavedDraftGuard, type GuardedNav } from "../useUnsavedDraftGuard";

function Probe({ dirty, onAttempt }: { dirty: boolean; onAttempt: (n: GuardedNav) => void }) {
  useUnsavedDraftGuard(dirty, onAttempt);
  return (
    <div>
      <a href="/profile" data-testid="internal">
        x
      </a>
      <a href="https://example.org/x" data-testid="external">
        x
      </a>
      <a href="/profile" target="_blank" data-testid="blank">
        x
      </a>
    </div>
  );
}

function click(el: Element, init: MouseEventInit = {}) {
  const ev = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...init });
  el.dispatchEvent(ev);
  return ev;
}

describe("useUnsavedDraftGuard", () => {
  it("while dirty: an internal link is held back and handed to the page", () => {
    const onAttempt = vi.fn();
    const { getByTestId } = render(<Probe dirty onAttempt={onAttempt} />);
    const ev = click(getByTestId("internal"));
    expect(ev.defaultPrevented).toBe(true);
    expect(onAttempt).toHaveBeenCalledWith({ kind: "href", href: "/profile" });
  });

  it("while dirty: a reload / tab close is held back (beforeunload)", () => {
    render(<Probe dirty onAttempt={vi.fn()} />);
    const ev = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
  });

  it("clean: nothing is intercepted", () => {
    const onAttempt = vi.fn();
    const { getByTestId } = render(<Probe dirty={false} onAttempt={onAttempt} />);
    const anchor = getByTestId("internal");
    anchor.addEventListener("click", (e) => e.preventDefault()); // keep jsdom from navigating
    click(anchor);
    expect(onAttempt).not.toHaveBeenCalled();
    const ev = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
  });

  it("internalNavTarget leaves external, new-tab and modified clicks alone", () => {
    const { getByTestId } = render(<Probe dirty={false} onAttempt={vi.fn()} />);
    const here = window.location;
    const mk = (init: MouseEventInit = {}) => new MouseEvent("click", { button: 0, ...init });
    expect(internalNavTarget(getByTestId("external") as HTMLAnchorElement, mk(), here)).toBeNull();
    expect(internalNavTarget(getByTestId("blank") as HTMLAnchorElement, mk(), here)).toBeNull();
    expect(internalNavTarget(getByTestId("internal") as HTMLAnchorElement, mk({ ctrlKey: true }), here)).toBeNull();
    expect(internalNavTarget(getByTestId("internal") as HTMLAnchorElement, mk(), here)).toBe("/profile");
  });
});
