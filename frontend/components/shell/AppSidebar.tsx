"use client";

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

import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

interface NavItem {
  key: "dashboard" | "profile" | "import" | "documents" | "settings" | "admin";
  href: string;
  icon: string;
}

const NAV_ITEMS: NavItem[] = [
  { key: "dashboard", href: "/dashboard",        icon: "dashboard"    },
  { key: "profile",   href: "/profile",          icon: "person_book"  },
  { key: "import",    href: "/profile/upload",   icon: "upload_file"  },
  { key: "documents", href: "/documents",        icon: "description"  },
  { key: "settings",  href: "/settings",         icon: "settings"     },
  { key: "admin",     href: "/admin/appearance", icon: "shield_person" },
];

interface AppSidebarProps {
  userName?: string | null;
}

export function AppSidebar({ userName }: AppSidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const t = useTranslations("shell");

  const initials = userName
    ? userName.split(" ").filter(Boolean).map((w) => w[0]).slice(0, 2).join("").toUpperCase()
    : "";

  return (
    <aside className="w-60 min-w-[240px] bg-white border-r border-gray-200 flex flex-col h-full">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-[18px] border-b border-gray-100">
        <img
          src="/applire-icon.png"
          alt="Applire"
          className="w-[34px] h-[34px] rounded-[9px] object-contain flex-shrink-0"
        />
        <span className="text-[16px] font-extrabold text-primary tracking-tight font-manrope">
          Applire
        </span>
      </div>

      {/* User strip — only shown when the profile fetch returned a name */}
      {userName ? (
        <div
          data-testid="sidebar-user-strip"
          className="flex items-center gap-2.5 px-5 py-3 border-b border-gray-100"
        >
          <div className="w-[34px] h-[34px] rounded-full bg-gradient-to-br from-primary-container to-surface-container-highest flex items-center justify-center text-[13px] font-bold text-primary flex-shrink-0">
            {initials}
          </div>
          <div className="min-w-0">
            <p className="text-[13px] font-bold text-gray-900 truncate">
              {userName}
            </p>
          </div>
        </div>
      ) : null}

      {/* Nav */}
      <nav className="flex-1 px-3 py-2.5 flex flex-col gap-0.5">
        {NAV_ITEMS.map(({ key, href, icon }) => {
          const active =
            pathname === href ||
            (pathname.startsWith(href + "/") &&
              !NAV_ITEMS.some(
                ({ href: h }) =>
                  h !== href && (pathname === h || pathname.startsWith(h + "/"))
              ));
          return (
            <button
              key={key}
              onClick={() => router.push(href)}
              className={cn(
                "flex items-center gap-2.5 w-full px-3 py-2.5 rounded-lg text-[13px] font-medium transition-colors text-left",
                active
                  ? "bg-primary-container text-primary font-bold border-r-[3px] border-primary rounded-r-none"
                  : "text-gray-600 hover:bg-surface-container hover:text-primary"
              )}
            >
              <span
                className="material-symbols-outlined flex-shrink-0"
                style={{ fontSize: 20, fontVariationSettings: active ? "'FILL' 1" : "'FILL' 0" }}
              >
                {icon}
              </span>
              {t(key)}
            </button>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-5 py-3 border-t border-gray-100">
        <p
          data-testid="sidebar-version"
          className="text-[10px] text-center text-outline-variant"
        >
          {process.env.NEXT_PUBLIC_APP_VERSION}
        </p>
      </div>
    </aside>
  );
}
