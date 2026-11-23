"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/** Board A1 — the first application without a plan. */

import { useTranslations } from "next-intl";

import { ActionButton, Dialog } from "@/components/account/Dialog";
import type { Allowance, CatalogPlan } from "./api";
import { PlanTiles } from "./PlanTiles";

export function ChoosePlanDialog({
  data,
  onLater,
  onChoose,
}: {
  data: Allowance;
  onLater: () => void;
  onChoose: (plan: CatalogPlan) => void;
}) {
  const t = useTranslations("plan");
  // The Community seam: nothing without the capability and a catalog.
  if (!data.purchasing_available || !data.catalog) return null;
  return (
    <Dialog
      open
      wide
      onClose={onLater}
      title={t("chooseTitle")}
      testId="choose-plan-dialog"
      actions={
        <ActionButton data-testid="choose-plan-later" onClick={onLater}>
          {t("later")}
        </ActionButton>
      }
    >
      <p>{t("testLine")}</p>
      <PlanTiles data={data} onChoose={onChoose} />
    </Dialog>
  );
}
