"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/** Board A1's tiles and footer sentence — in the dialog and inline in the plan card. */

import { useLocale, useTranslations } from "next-intl";

import { ActionButton } from "@/components/account/Dialog";
import type { Allowance, CatalogPlan } from "./api";
import { formatPrice } from "./format";
import { plansAscending } from "./helpers";

export function PlanTiles({ data, onChoose }: { data: Allowance; onChoose: (plan: CatalogPlan) => void }) {
  const t = useTranslations("plan");
  const locale = useLocale();
  const plans = plansAscending(data);
  if (plans.length === 0) return null;
  return (
    <div data-testid="plan-tiles">
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {plans.map((p) => (
          <div key={p.id} data-testid={`plan-tile-${p.id}`} className="flex flex-col gap-1 rounded-xl border border-outline-variant bg-white p-4">
            <span className="text-[15px] font-bold text-on-surface">{p.label}</span>
            <span>{t("perMonth", { count: p.applications })}</span>
            <span className="font-semibold text-on-surface">{t("pricePerMonth", { price: formatPrice(p.price_cents, locale) })}</span>
            <ActionButton tone="primary" className="mt-2" data-testid={`plan-choose-${p.id}`} onClick={() => onChoose(p)}>
              {t("choosePlan", { plan: p.label })}
            </ActionButton>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[13px]">{t("chooseFooter", { up: plans[plans.length - 1].label, down: plans[0].label })}</p>
    </div>
  );
}
