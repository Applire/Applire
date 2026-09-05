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

import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { NAV_ITEMS } from "./nav-items";

interface AppSidebarProps {
  userName?: string | null;
}

/**
 * The two document result routes. ADR-081 clause 1 collapses the app nav to a
 * 56 px icon rail here — expandable, never removed. E058/US299.
 *
 * The match lives in this component rather than in the shell layout on purpose:
 * the layout mounts ONE sidebar and the sidebar decides its own shape, so no
 * other route can acquire the rail by accident. arc42 §5.3.21 records this as
 * the first time the shell's shape depends on the ROUTE and not only on the
 * breakpoint — ADR-050 §1 had assumed one shell shape per breakpoint, and a
 * future page that wants the same treatment must say so rather than inherit it.
 */
const DOCUMENT_ROUTE_RE = /^\/flow\/[^/]+\/(cv|cover-letter)\/?$/;

export function isDocumentRoute(pathname: string | null | undefined): boolean {
  return DOCUMENT_ROUTE_RE.test(pathname ?? "");
}

export function AppSidebar({ userName }: AppSidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const t = useTranslations("shell");
  // The rail is a collapse, not a removal: one labelled control expands it, and
  // the expansion lives only for this visit (no preference, no server state).
  const [railExpanded, setRailExpanded] = useState(false);

  const rail = isDocumentRoute(pathname) && !railExpanded;

  const initials = userName
    ? userName.split(" ").filter(Boolean).map((w) => w[0]).slice(0, 2).join("").toUpperCase()
    : "";

  const isActive = (href: string) =>
    pathname === href ||
    (pathname.startsWith(href + "/") &&
      !NAV_ITEMS.some(
        ({ href: h }) => h !== href && (pathname === h || pathname.startsWith(h + "/")),
      ));

  if (rail) {
    return (
      // F9.1 (#76): keep the primary nav above slide-over drawers (z-50).
      <aside
        data-testid="app-sidebar"
        data-variant="rail"
        className="relative z-[60] w-14 min-w-[56px] bg-white border-r border-gray-200 hidden md:flex md:flex-col h-full items-center"
      >
        <img
          src="/applire-icon.png"
          alt={t("appName")}
          className="w-[30px] h-[30px] rounded-[8px] object-contain flex-shrink-0 my-3"
        />
        <button
          type="button"
          data-testid="app-sidebar-rail-expand"
          onClick={() => setRailExpanded(true)}
          aria-expanded={false}
          aria-label={t("railExpand")}
          title={t("railExpand")}
          className="w-9 h-9 flex items-center justify-center rounded-lg text-gray-500 hover:bg-surface-container hover:text-primary"
        >
          <span className="material-symbols-outlined" style={{ fontSize: 20 }} aria-hidden="true">
            {MENU_ICON}
          </span>
        </button>
        <nav className="flex-1 flex flex-col gap-1 py-2">
          {NAV_ITEMS.map(({ key, href, icon }) => {
            const active = isActive(href);
            return (
              <button
                key={key}
                onClick={() => router.push(href)}
                aria-label={t(key)}
                title={t(key)}
                className={cn(
                  "w-9 h-9 flex items-center justify-center rounded-lg transition-colors",
                  active
                    ? "bg-primary-container text-primary"
                    : "text-gray-600 hover:bg-surface-container hover:text-primary",
                )}
              >
                <span
                  className="material-symbols-outlined"
                  style={{ fontSize: 20, fontVariationSettings: active ? "'FILL' 1" : "'FILL' 0" }}
                  aria-hidden="true"
                >
                  {icon}
                </span>
              </button>
            );
          })}
        </nav>
      </aside>
    );
  }

  return (
    // F9.1 (#76): keep the primary nav above slide-over drawers (z-50) so an open
    // enrichment/review panel never traps navigation — the sidebar stays clickable.
    <aside
      data-testid="app-sidebar"
      data-variant="full"
      className="relative z-[60] w-60 min-w-[240px] bg-white border-r border-gray-200 hidden md:flex md:flex-col h-full"
    >
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-[18px] border-b border-gray-100">
        <img
          src="/applire-icon.png"
          alt={t("appName")}
          className="w-[34px] h-[34px] rounded-[9px] object-contain flex-shrink-0"
        />
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- brand name not translated */}
        <span className="text-[16px] font-extrabold text-primary tracking-tight font-manrope">Applire</span>
        {/* On a document route the rail is the default; this control puts it
            back. Never a removal — ADR-081 cl. 1. */}
        {railExpanded && (
          <button
            type="button"
            data-testid="app-sidebar-rail-collapse"
            onClick={() => setRailExpanded(false)}
            aria-expanded
            aria-label={t("railCollapse")}
            title={t("railCollapse")}
            className="ml-auto w-7 h-7 flex items-center justify-center rounded text-gray-500 hover:bg-surface-container hover:text-primary"
          >
            <span className="material-symbols-outlined" style={{ fontSize: 18 }} aria-hidden="true">
              {COLLAPSE_ICON}
            </span>
          </button>
        )}
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
          const active = isActive(href);
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

// Non-user-facing Material Symbols identifiers — JS consts to avoid the JSX literal rule
const MENU_ICON = "menu";
const COLLAPSE_ICON = "left_panel_close";
