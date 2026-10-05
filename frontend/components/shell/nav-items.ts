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

export interface NavItem {
  key: "dashboard" | "profile" | "import" | "documents" | "settings" | "admin";
  href: string;
  icon: string;
  /** US330: shown to admins only (Administration → /admin/users, G-2). */
  adminOnly?: boolean;
  /** Path prefix that marks the item active (default: `href`). */
  activePrefix?: string;
}

/**
 * Primary navigation entries. Single source of truth shared by the desktop
 * AppSidebar (components/shell/AppSidebar.tsx) and the below-md
 * MobileNavDrawer (components/shell/MobileNavDrawer.tsx) so the two nav
 * surfaces can never drift apart (US223).
 */
export const NAV_ITEMS: NavItem[] = [
  { key: "dashboard", href: "/dashboard",        icon: "dashboard"    },
  { key: "profile",   href: "/profile",          icon: "person_book"  },
  { key: "import",    href: "/profile/upload",   icon: "upload_file"  },
  { key: "documents", href: "/documents",        icon: "description"  },
  { key: "settings",  href: "/settings",         icon: "settings"     },
  { key: "admin",     href: "/admin/users",      icon: "shield_person", adminOnly: true, activePrefix: "/admin" },
];

/** The entries this person sees — Administration only for admins (G-2). */
export function navItemsFor(isAdmin: boolean): NavItem[] {
  return NAV_ITEMS.filter((item) => !item.adminOnly || isAdmin);
}

/**
 * Exact match, or a sub-path of the item's prefix that no OTHER item claims
 * more specifically (/profile/upload belongs to "import", not "profile").
 */
export function isNavItemActive(pathname: string | null | undefined, item: NavItem): boolean {
  const path = pathname ?? "";
  const prefixOf = (i: NavItem) => i.activePrefix ?? i.href;
  const own = prefixOf(item);
  if (path === item.href || path === own) return true;
  if (!path.startsWith(own + "/")) return false;
  return !NAV_ITEMS.some((other) => {
    if (other === item) return false;
    const p = prefixOf(other);
    return p.length > own.length && (path === p || path.startsWith(p + "/"));
  });
}
