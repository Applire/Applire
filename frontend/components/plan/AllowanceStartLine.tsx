"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/** Board A3 — the allowance line at the start of a new application. */

import { useLocale, useTranslations } from "next-intl";

import { formatDay, formatMonthBefore } from "./format";
import { useAllowance } from "./useAllowance";

export function AllowanceStartLine() {
  const t = useTranslations("allowance");
  const tPlan = useTranslations("plan");
  const locale = useLocale();
  const { data } = useAllowance();
  const apps = data?.applications;
  if (!data || data.policy !== "allowance" || !apps) return null;
  // Without a plan the plan choice (A1) speaks, not this line.
  if (data.purchasing_available && !apps.has_plan) return null;

  let text: string;
  if (apps.remaining >= 2) {
    text = t("startLine", { left: apps.remaining, limit: apps.limit });
  } else if (apps.remaining === 1) {
    text = t("startLast", { limit: apps.limit, date: formatDay(data.renews_on, locale) });
  } else if (apps.purchased_remaining > 0) {
    text = tPlan("boughtLine", { count: apps.purchased_remaining });
  } else {
    text = t("zeroTitle", { month: formatMonthBefore(data.renews_on, locale) });
  }
  return (
    <p data-testid="allowance-start-line" className="text-[13px] text-on-surface-variant">
      {text}
    </p>
  );
}
export { offerPlanChoice } from "./events";
