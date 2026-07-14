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

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { MobileNavDrawer } from "./MobileNavDrawer";
import { useShellUser } from "./ShellUserContext";

type IntlKey = string;

interface SectionMode {
  mode: "section";
  titleKey: IntlKey;
  showSearch?: boolean;
  searchValue?: string;
  onSearchChange?: (q: string) => void;
  searchPlaceholderKey?: IntlKey;
}

interface DetailMode {
  mode: "detail";
  backHref: string;
  backLabelKey: IntlKey;
  pageTitle: string;
}

interface FlowStep {
  key: string;
  labelKey: IntlKey;
  state: "done" | "active" | "pending";
}

interface FlowMode {
  mode: "flow";
  steps: FlowStep[];
  trailingBadge?: string;
}

type AppTopbarProps = SectionMode | DetailMode | FlowMode;

export function AppTopbar(props: AppTopbarProps) {
  const router = useRouter();
  const t = useTranslations();
  const { userName } = useShellUser();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const activeStepRef = useRef<HTMLSpanElement | null>(null);

  const initials = userName
    ? userName.split(" ").filter(Boolean).map((w) => w[0]).slice(0, 2).join("").toUpperCase()
    : null;

  // US225: at 390px four full-text step pills don't all fit next to the
  // hamburger + bell + avatar — the strip scrolls horizontally (overflow-x-auto
  // below), so make sure the ACTIVE step is the one in view instead of
  // whichever one happened to render first.
  const activeStepKey = props.mode === "flow"
    ? props.steps.find((s) => s.state === "active")?.key
    : undefined;
  useEffect(() => {
    // jsdom (unit tests) doesn't implement scrollIntoView — guard defensively
    // rather than assume every render environment has it.
    if (activeStepKey && typeof activeStepRef.current?.scrollIntoView === "function") {
      activeStepRef.current.scrollIntoView({ inline: "center", block: "nearest" });
    }
  }, [activeStepKey]);

  return (
    <>
      <header className="h-[52px] bg-white/90 backdrop-blur border-b border-gray-200 flex items-center px-6 gap-4 flex-shrink-0">
        {/* US223: below md the persistent AppSidebar is hidden — this hamburger
            opens the equivalent drawer navigation (MobileNavDrawer). */}
        <button
          type="button"
          aria-label={t("shell.openNavAriaLabel")}
          onClick={() => setDrawerOpen(true)}
          className="md:hidden -ml-2 w-8 h-8 rounded-full flex items-center justify-center text-gray-600 hover:bg-surface-container hover:text-primary transition-colors flex-shrink-0"
        >
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
          <span className="material-symbols-outlined" style={{ fontSize: 22 }}>menu</span>
        </button>

        <div className="flex items-center gap-3 flex-1 min-w-0">
          {props.mode === "section" && (
            <h1 className="text-[15px] font-extrabold text-neutral-dark font-manrope truncate">
              {t(props.titleKey)}
            </h1>
          )}

          {props.mode === "detail" && (
            <>
              <Link
                href={props.backHref}
                className="text-[13px] text-teal hover:underline flex-shrink-0"
              >
                {t("shell.backArrowLabel", { label: t(props.backLabelKey) })}
              </Link>
              <span className="text-gray-300" aria-hidden>{t("shell.topbarSeparator")}</span>
              <h1 className="text-[15px] font-extrabold text-neutral-dark font-manrope truncate">
                {props.pageTitle}
              </h1>
            </>
          )}

          {props.mode === "flow" && (
            // US225: below md the pills shrink and the trailing role-title
            // badge (decorative context, not a step) is dropped so the active
            // step stays reachable within 390px instead of relying solely on
            // the horizontal scroll for every pill.
            <div className="flex items-center gap-1 sm:gap-1.5 overflow-x-auto">
              {props.steps.map((s) => (
                <span
                  key={s.key}
                  ref={s.state === "active" ? activeStepRef : undefined}
                  className={cn(
                    "px-2 md:px-3 py-0.5 md:py-1 rounded-full text-[11px] md:text-[12px] font-semibold whitespace-nowrap shrink-0",
                    s.state === "done"   && "bg-success text-white",
                    s.state === "active" && "bg-primary text-white",
                    s.state === "pending" && "bg-surface-container text-gray-500",
                  )}
                >
                  {t(s.labelKey)}
                </span>
              ))}
              {props.trailingBadge && (
                <span
                  className="hidden md:inline-block ml-2 px-2.5 py-1 rounded-full text-[12px] text-gray-600 bg-gray-100 max-w-[260px] truncate shrink-0"
                  title={props.trailingBadge}
                >
                  {props.trailingBadge}
                </span>
              )}
            </div>
          )}
        </div>

        {props.mode === "section" && props.showSearch && (
          <div className="flex items-center gap-2 bg-surface-container border border-gray-200 rounded-full px-3.5 py-1.5 w-52">
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
            <span className="material-symbols-outlined text-gray-400" style={{ fontSize: 16 }}>search</span>
            <input
              type="text"
              value={props.searchValue ?? ""}
              onChange={(e) => props.onSearchChange?.(e.target.value)}
              placeholder={props.searchPlaceholderKey ? t(props.searchPlaceholderKey) : ""}
              className="bg-transparent border-none outline-none text-[12.5px] text-gray-800 placeholder:text-gray-400 w-full"
            />
          </div>
        )}

        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            type="button"
            aria-label={t("shell.notificationsAriaLabel")}
            className="w-8 h-8 rounded-full flex items-center justify-center text-gray-600 hover:bg-surface-container hover:text-primary transition-colors"
          >
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
            <span className="material-symbols-outlined" style={{ fontSize: 20 }}>notifications</span>
          </button>
          <button
            type="button"
            aria-label={t("shell.openSettingsAriaLabel")}
            onClick={() => router.push("/settings")}
            className="w-[30px] h-[30px] rounded-full bg-gradient-to-br from-primary-container to-surface-container-highest flex items-center justify-center text-[12px] font-bold text-primary cursor-pointer"
          >
            {/* Below md: real initials (US223). md and up: unchanged — the
                same placeholder letter AppTopbar always showed here. */}
            <span className="md:hidden" data-testid="topbar-avatar-mobile">
              {initials ?? t("shell.topbarUserInitial")}
            </span>
            <span className="hidden md:inline" data-testid="topbar-avatar-desktop">
              {t("shell.topbarUserInitial")}
            </span>
          </button>
        </div>
      </header>
      <MobileNavDrawer open={drawerOpen} onOpenChange={setDrawerOpen} />
    </>
  );
}
