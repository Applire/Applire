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
  { key: "admin",     href: "/admin/appearance", icon: "shield_person" },
];
