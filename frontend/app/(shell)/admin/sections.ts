// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The admin area's section registry — founder ruling G-3 (2026-10-05).
 *
 * ONE array drives the sub-nav tabs, the topbar title and the `/admin` landing
 * redirect. Adding a section (the announced "Vorlagen" — activate/deactivate and
 * import CV and cover-letter templates) is one entry here plus one
 * `app/(shell)/admin/<key>/page.tsx`; the layout is not edited.
 */

export interface AdminSection {
  /** Route segment and test id suffix: `/admin/<key>`, `admin-nav-<key>`. */
  key: string;
  href: string;
  /** Key in the `adminNav` catalog namespace. */
  labelKey: string;
}

export const ADMIN_SECTIONS: readonly AdminSection[] = [
  { key: "users", href: "/admin/users", labelKey: "users" },
  { key: "appearance", href: "/admin/appearance", labelKey: "appearance" },
  { key: "monitoring", href: "/admin/monitoring", labelKey: "monitoring" },
];

/** The section a pathname belongs to (longest href prefix), or the first one. */
export function activeSection(pathname: string | null | undefined): AdminSection {
  const p = pathname ?? "";
  const match = [...ADMIN_SECTIONS]
    .sort((a, b) => b.href.length - a.href.length)
    .find((s) => p === s.href || p.startsWith(s.href + "/"));
  return match ?? ADMIN_SECTIONS[0];
}
