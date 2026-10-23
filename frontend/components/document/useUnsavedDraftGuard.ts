// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

// #737 / adversarial finding 11 (Strawberry build 2, WP-E fix) — while a
// document page holds an unsaved section or letter draft, every way off the
// page is held back:
//
//  * a reload, closing the tab, or a hard navigation → the browser's own
//    `beforeunload` prompt (the only thing a page may show there);
//  * an in-app link (the shell nav, the flow stepper, any Next `<Link>`) → the
//    click is stopped in the capture phase and handed to `onAttempt`, which
//    opens the page's existing unsaved-draft dialog;
//  * a programmatic in-app navigation from the shell chrome (the sidebar's nav
//    buttons, the phone nav drawer, the user menu — `router.push`, no anchor,
//    so the click guard never saw them: M5, the UX audit 2026-10-20 lost a
//    draft that way) → `useGuardedPush()` hands it to the same `onAttempt`;
//  * the browser's back button inside the app → a same-URL guard entry is
//    pushed while dirty; leaving it fires `popstate` on the SAME page (Next
//    restores the same tree), and `onAttempt({ kind: "back" })` asks. The page
//    proceeds with `history.back()` after a discard, or re-arms by calling
//    `rearmBack()` when the user keeps editing.
//
// The guard only intercepts; what the dialog says and does stays the page's.
"use client";

import { useCallback, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";

export type GuardedNav = { kind: "href"; href: string } | { kind: "back" };

const GUARD_MARK = "applireDraftGuard";

/** An internal, same-tab navigation to ANOTHER page — the only clicks the guard takes. */
export function internalNavTarget(anchor: HTMLAnchorElement, event: MouseEvent, here: Location): string | null {
  if (event.defaultPrevented || event.button !== 0) return null;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
  const target = anchor.getAttribute("target");
  if (target && target !== "_self") return null;
  if (anchor.hasAttribute("download")) return null;
  const raw = anchor.getAttribute("href");
  if (!raw || raw.startsWith("#") || raw.startsWith("mailto:") || raw.startsWith("tel:")) return null;
  const url = new URL(anchor.href, here.href);
  if (url.origin !== here.origin) return null;
  if (url.pathname === here.pathname && url.search === here.search) return null;
  return url.pathname + url.search + url.hash;
}

// The pages whose guard is armed right now (normally at most one). A
// programmatic navigation asks the most recently armed one.
const activeGuards: Array<(nav: GuardedNav) => void> = [];

/**
 * Navigate in-app the way a guarded link click does: while a page holds an
 * unsaved draft, the target is handed to that page's `onAttempt` (its own
 * unsaved-draft dialog) instead of being pushed. The ONE way shell chrome
 * navigates programmatically — never call `router.push` there directly.
 */
export function guardedNavigate(href: string, push: (href: string) => void): void {
  const attempt = activeGuards.at(-1);
  if (attempt) {
    const here = window.location;
    const url = new URL(href, here.href);
    const samePage = url.origin === here.origin && url.pathname === here.pathname && url.search === here.search;
    if (!samePage) {
      attempt({ kind: "href", href: url.pathname + url.search + url.hash });
      return;
    }
  }
  push(href);
}

/** `router.push` behind the unsaved-draft guard — for buttons that navigate. */
export function useGuardedPush(): (href: string) => void {
  const router = useRouter();
  return useCallback((href: string) => guardedNavigate(href, (h) => router.push(h)), [router]);
}

export function useUnsavedDraftGuard(dirty: boolean, onAttempt: (nav: GuardedNav) => void) {
  const attemptRef = useRef(onAttempt);
  useEffect(() => {
    attemptRef.current = onAttempt;
  });
  const armed = useRef(false);

  // Programmatic navigation (guardedNavigate) while dirty.
  useEffect(() => {
    if (!dirty) return;
    const attempt = (nav: GuardedNav) => attemptRef.current(nav);
    activeGuards.push(attempt);
    return () => {
      const i = activeGuards.indexOf(attempt);
      if (i >= 0) activeGuards.splice(i, 1);
    };
  }, [dirty]);

  // Page unload: reload, tab close, typed URL, external link.
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Legacy browsers read the return value.
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  // In-app links, in the capture phase so Next's Link handler never runs.
  useEffect(() => {
    if (!dirty) return;
    const onClick = (e: MouseEvent) => {
      const anchor = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!anchor) return;
      const href = internalNavTarget(anchor, e, window.location);
      if (!href) return;
      e.preventDefault();
      e.stopPropagation();
      attemptRef.current({ kind: "href", href });
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [dirty]);

  const arm = useCallback(() => {
    if (armed.current) return;
    armed.current = true;
    window.history.pushState({ [GUARD_MARK]: true }, "", window.location.href);
  }, []);

  // Browser back: a guard entry with the same URL sits on top while dirty.
  useEffect(() => {
    if (!dirty) return;
    arm();
    const onPopState = () => {
      if (!armed.current) return;
      // We just left the guard entry — still on this page.
      armed.current = false;
      attemptRef.current({ kind: "back" });
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [dirty, arm]);

  // Draft saved or discarded while the guard entry is still on top: drop it,
  // so the next back press leaves the page as the user expects.
  useEffect(() => {
    if (dirty || !armed.current) return;
    armed.current = false;
    // Not keyed on our mark in history.state: Next's router replaces the
    // current entry's state on its own updates, so the mark does not survive.
    // `armed` is the record that our entry is on top (popstate clears it).
    window.history.back();
  }, [dirty]);

  return {
    /** Keep editing after a back-press: put the guard entry back. */
    rearmBack: arm,
    /**
     * The page is about to navigate away itself (a confirmed link or back):
     * leave the guard entry where it is instead of popping it, which would
     * race the navigation.
     */
    release: () => {
      armed.current = false;
    },
    /**
     * The draft ends while the page stays (Hazelnut b3 MD3-1: the letter form
     * is cancelled or submitted — it unmounts while its fields still differ,
     * so the "dirty → false" pop above never runs). Pops the guard entry and
     * resolves once the browser has traversed, so a navigation the caller
     * starts next cannot be undone by the pop. Resolves at once when nothing
     * is armed; a traversal that never reports resolves after 400 ms.
     */
    disarm: () =>
      new Promise<void>((resolve) => {
        if (!armed.current) {
          resolve();
          return;
        }
        armed.current = false;
        let done = false;
        const finish = () => {
          if (done) return;
          done = true;
          window.removeEventListener("popstate", finish);
          resolve();
        };
        window.addEventListener("popstate", finish);
        window.setTimeout(finish, 400);
        window.history.back();
      }),
  };
}
