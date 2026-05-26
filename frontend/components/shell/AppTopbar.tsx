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

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

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

  return (
    <header className="h-[52px] bg-white/90 backdrop-blur border-b border-gray-200 flex items-center px-6 gap-4 flex-shrink-0">
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
              ← {t(props.backLabelKey)}
            </Link>
            <span className="text-gray-300" aria-hidden>|</span>
            <h1 className="text-[15px] font-extrabold text-neutral-dark font-manrope truncate">
              {props.pageTitle}
            </h1>
          </>
        )}

        {props.mode === "flow" && (
          <div className="flex items-center gap-1.5 overflow-x-auto">
            {props.steps.map((s) => (
              <span
                key={s.key}
                className={cn(
                  "px-3 py-1 rounded-full text-[12px] font-semibold whitespace-nowrap",
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
                className="ml-2 px-2.5 py-1 rounded-full text-[12px] text-gray-600 bg-gray-100 max-w-[260px] truncate"
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
          <span className="material-symbols-outlined" style={{ fontSize: 20 }}>notifications</span>
        </button>
        <button
          type="button"
          aria-label={t("shell.openSettingsAriaLabel")}
          onClick={() => router.push("/settings")}
          className="w-[30px] h-[30px] rounded-full bg-gradient-to-br from-primary-container to-surface-container-highest flex items-center justify-center text-[12px] font-bold text-primary cursor-pointer"
        >
          A
        </button>
      </div>
    </header>
  );
}
