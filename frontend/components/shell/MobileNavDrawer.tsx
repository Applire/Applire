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
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { NAV_ITEMS } from "./nav-items";
import { useShellUser } from "./ShellUserContext";

interface MobileNavDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Below-md drawer navigation (US223): built on the existing generic
 * components/ui/sheet.tsx (side="left") rather than forking a second nav
 * surface. Mirrors AppSidebar's nav list (shared via ./nav-items) with the
 * wordmark moved into the drawer header.
 *
 * F9.1 (#76): the primary nav must stay above z-50 slide-over panels so an
 * open panel never traps navigation — both the content and the overlay carry
 * an explicit z-[60] override (sheet.tsx primitives default to z-50).
 */
export function MobileNavDrawer({ open, onOpenChange }: MobileNavDrawerProps) {
  const pathname = usePathname();
  const router = useRouter();
  const t = useTranslations("shell");
  const { userName } = useShellUser();

  const initials = userName
    ? userName.split(" ").filter(Boolean).map((w) => w[0]).slice(0, 2).join("").toUpperCase()
    : "";

  function navigate(href: string) {
    onOpenChange(false);
    router.push(href);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="left"
        data-testid="mobile-nav-drawer"
        overlayClassName="z-[60]"
        className="z-[60] w-72 max-w-[85vw] gap-0 p-0 flex flex-col bg-white"
      >
        <div className="flex items-center gap-2.5 px-5 py-[18px] border-b border-gray-100">
          <img
            src="/applire-icon.png"
            alt={t("appName")}
            className="w-[34px] h-[34px] rounded-[9px] object-contain flex-shrink-0"
          />
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- brand name not translated */}
          <SheetTitle className="text-[16px] font-extrabold text-primary tracking-tight font-manrope">Applire</SheetTitle>
        </div>
        <SheetDescription className="sr-only">{t("navDrawerDescription")}</SheetDescription>

        {userName ? (
          <div
            data-testid="drawer-user-strip"
            className="flex items-center gap-2.5 px-5 py-3 border-b border-gray-100"
          >
            <div className="w-[34px] h-[34px] rounded-full bg-gradient-to-br from-primary-container to-surface-container-highest flex items-center justify-center text-[13px] font-bold text-primary flex-shrink-0">
              {initials}
            </div>
            <div className="min-w-0">
              <p className="text-[13px] font-bold text-gray-900 truncate">{userName}</p>
            </div>
          </div>
        ) : null}

        <nav className="flex-1 px-3 py-2.5 flex flex-col gap-0.5 overflow-y-auto">
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
                type="button"
                onClick={() => navigate(href)}
                className={cn(
                  "flex items-center gap-2.5 w-full px-3 py-2.5 rounded-lg text-[13px] font-medium transition-colors text-left",
                  active
                    ? "bg-primary-container text-primary font-bold"
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
      </SheetContent>
    </Sheet>
  );
}
