"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/** The subscription summary (shaped like board A6): plan, price, "Zu zahlen 0,00 €". */

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { ActionButton, Dialog, DialogError } from "@/components/account/Dialog";
import { putPlan, type CatalogPlan, type PlanChange } from "./api";
import { announceAllowanceChanged } from "./events";
import { formatPrice } from "./format";

export function PlanSummaryDialog({
  plan,
  onBack,
  onClose,
  onDone,
}: {
  plan: CatalogPlan;
  /** Back to the choice (A1); without it "Zurück" just closes. */
  onBack?: () => void;
  onClose: () => void;
  onDone?: (change: PlanChange) => void;
}) {
  const t = useTranslations("plan");
  const locale = useLocale();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function confirm() {
    setBusy(true);
    setFailed(false);
    const res = await putPlan(plan.id);
    setBusy(false);
    if (!res.ok) {
      setFailed(true);
      return;
    }
    announceAllowanceChanged(res.change);
    onDone?.(res.change);
    onClose();
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("summaryTitle")}
      testId="plan-summary-dialog"
      actions={
        <>
          <ActionButton data-testid="plan-summary-back" onClick={onBack ?? onClose}>
            {t("back")}
          </ActionButton>
          <ActionButton tone="primary" data-testid="plan-summary-confirm" disabled={busy} onClick={() => void confirm()}>
            {t("completePlan")}
          </ActionButton>
        </>
      }
    >
      <p>{t("testLine")}</p>
      <div className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2">
        <span className="col-span-2 font-semibold text-on-surface">
          {t("summaryPlanRow", { plan: plan.label, price: formatPrice(plan.price_cents, locale) })}
        </span>
        <span>{t("paymentLabel")}</span>
        <span className="text-on-surface">{t("paymentNone")}</span>
        <span>{t("dueLabel")}</span>
        <span data-testid="plan-summary-due" className="font-semibold text-on-surface">
          {formatPrice(0, locale)}
        </span>
      </div>
      <p className="mt-4 text-[13px]">{t("expireNote")}</p>
      {failed && <DialogError testId="plan-summary-error">{t("error")}</DialogError>}
    </Dialog>
  );
}
