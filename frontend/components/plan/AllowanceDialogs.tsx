"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Boards A4 and A8 (+ the 503 "unavailable" dialog, not in the mock). Every one is
 * informational: the refused action is not retried — the person presses it again.
 */

import { useLocale, useTranslations } from "next-intl";

import { ActionButton, Dialog } from "@/components/account/Dialog";
import type { Allowance, CatalogPlan } from "./api";
import { formatDay, formatMonthBefore } from "./format";
import { plansAscending } from "./helpers";

function CloseButton({ onClose }: { onClose: () => void }) {
  const t = useTranslations("allowance");
  return (
    <ActionButton data-testid="allowance-close" onClick={onClose}>
      {t("close")}
    </ActionButton>
  );
}

/** A4: the month's applications are used up. */
export function ZeroDialog({
  data,
  renewsOn,
  onClose,
  onBuy,
  onUpgrade,
}: {
  data: Allowance;
  renewsOn?: string | null;
  onClose: () => void;
  onBuy: () => void;
  onUpgrade: (plan: CatalogPlan) => void;
}) {
  const t = useTranslations("allowance");
  const locale = useLocale();
  if (!data.applications) return null;
  const renews = renewsOn ?? data.renews_on;
  const purchasing = data.purchasing_available;
  // The upgrade offer: the catalog's largest plan, unless the person is on it.
  const plans = plansAscending(data);
  const largest = plans[plans.length - 1] ?? null;
  const upgrade = purchasing && largest && largest.id !== data.plan?.id ? largest : null;
  return (
    <Dialog
      open
      onClose={onClose}
      title={t("zeroTitle", { month: formatMonthBefore(renews, locale) })}
      testId="zero-dialog"
      actions={
        <>
          <CloseButton onClose={onClose} />
          {purchasing && (
            <ActionButton tone={upgrade ? "secondary" : "primary"} data-testid="zero-buy" onClick={onBuy}>
              {t("buy")}
            </ActionButton>
          )}
          {upgrade && (
            <ActionButton tone="primary" data-testid="zero-upgrade" onClick={() => onUpgrade(upgrade)}>
              {t("switchTo", { plan: upgrade.label })}
            </ActionButton>
          )}
        </>
      }
    >
      <p>{t("zeroRenew", { date: formatDay(renews, locale), count: data.applications.limit })}</p>
      <p className="mt-2">{t("zeroKeep")}</p>
      {upgrade && <p className="mt-2">{t("zeroOptions", { plan: upgrade.label, count: upgrade.applications })}</p>}
    </Dialog>
  );
}

/** A8: the month's CV imports are used up. */
export function ImportsZeroDialog({ data, renewsOn, onClose }: { data: Allowance; renewsOn?: string | null; onClose: () => void }) {
  const t = useTranslations("allowance");
  const locale = useLocale();
  if (!data.imports) return null;
  const renews = renewsOn ?? data.renews_on;
  return (
    <Dialog
      open
      onClose={onClose}
      title={t("importsTitle", { month: formatMonthBefore(renews, locale) })}
      testId="imports-zero-dialog"
      actions={<CloseButton onClose={onClose} />}
    >
      <p>{t("importsBody", { date: formatDay(renews, locale), count: data.imports.limit })}</p>
    </Dialog>
  );
}

/** A8: the instance's daily limit is reached. */
export function DailyLimitDialog({ onClose }: { onClose: () => void }) {
  const t = useTranslations("allowance");
  return (
    <Dialog open onClose={onClose} title={t("dailyTitle")} testId="daily-limit-dialog" actions={<CloseButton onClose={onClose} />}>
      <p>{t("dailyBody")}</p>
    </Dialog>
  );
}

/** 503 allowance_unavailable — the usage cannot be counted right now. */
export function UnavailableDialog({ onClose }: { onClose: () => void }) {
  const t = useTranslations("allowance");
  return (
    <Dialog open onClose={onClose} title={t("unavailable.title")} testId="unavailable-dialog" actions={<CloseButton onClose={onClose} />}>
      <p>{t("unavailable.body")}</p>
    </Dialog>
  );
}
