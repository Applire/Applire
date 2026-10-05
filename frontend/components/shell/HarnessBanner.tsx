// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

import { useTranslations } from "next-intl";

import { useCurrentUser } from "@/lib/auth/current-user";

/**
 * ADR-091 cl. 3(d) — the test harness is serving this instance: a red,
 * non-dismissable banner on EVERY page (shell and signed-out pages alike), so a
 * harness can never pass for a real install. Source: `/api/auth/state.harness`.
 */
export function HarnessBanner() {
  const t = useTranslations("shell");
  const { authState } = useCurrentUser();
  if (!authState?.harness) return null;
  return (
    <div
      role="alert"
      data-testid="harness-banner"
      className="flex flex-shrink-0 items-center justify-center gap-2 bg-critical px-4 py-1.5 text-center text-[12.5px] font-bold tracking-wide text-white"
    >
      <span className="material-symbols-outlined" style={{ fontSize: 16 }} aria-hidden="true">
        {HARNESS_ICON}
      </span>
      <span>{t("harnessBanner")}</span>
    </div>
  );
}

const HARNESS_ICON = "science";
