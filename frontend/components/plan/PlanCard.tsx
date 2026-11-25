"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Settings → "Dein Abo" (boards A2 / A7). Rendered only where the instance sells
 * simulated plans (`purchasing_available`); on a Community install nothing shows.
 */

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { ActionButton, DialogError } from "@/components/account/Dialog";
import { putPlan, type Allowance, type CatalogPlan } from "./api";
import { BuyDialog } from "./BuyDialog";
import { ALLOWANCE_CHANGED_EVENT, announceAllowanceChanged, type AllowanceChangedDetail } from "./events";
import { formatDay, formatMonthOf } from "./format";
import { neighbours } from "./helpers";
import { PlanSummaryDialog } from "./PlanSummaryDialog";
import { PlanTiles } from "./PlanTiles";
import { useAllowance } from "./useAllowance";

function Meter({ used, limit, testId }: { used: number; limit: number; testId: string }) {
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  return (
    <div aria-hidden className="mt-1 h-2 w-full overflow-hidden rounded-full bg-surface-container-high" data-testid={testId}>
      <div className="h-full rounded-full bg-teal" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function PlanCard() {
  const { data } = useAllowance();
  const [upgraded, setUpgraded] = useState(false);

  useEffect(() => {
    const onChanged = (e: Event) => {
      if ((e as CustomEvent<AllowanceChangedDetail>).detail?.change === "upgraded") setUpgraded(true);
    };
    window.addEventListener(ALLOWANCE_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(ALLOWANCE_CHANGED_EVENT, onChanged);
  }, []);

  if (!data?.purchasing_available || !data.applications) return null;
  return (
    <>
      {upgraded && data.plan && (
        <div role="status" data-testid="plan-upgraded-notice" className="rounded-xl border border-success/40 bg-success-container px-4 py-3 text-[14px] text-on-surface">
          <UpgradedText data={data} />
        </div>
      )}
      <PlanCardBody data={data} />
    </>
  );
}

function UpgradedText({ data }: { data: Allowance }) {
  const t = useTranslations("plan");
  return <>{t("upgraded", { plan: data.plan?.label ?? "", count: data.applications?.remaining ?? 0 })}</>;
}

function PlanCardBody({ data }: { data: Allowance }) {
  const t = useTranslations("plan");
  const locale = useLocale();
  const [summary, setSummary] = useState<CatalogPlan | null>(null);
  const [buyOpen, setBuyOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const apps = data.applications!;
  const renews = formatDay(data.renews_on, locale);
  const { up, down } = neighbours(data);

  async function changeDirect(planId: string) {
    setBusy(true);
    setFailed(false);
    const res = await putPlan(planId);
    setBusy(false);
    if (!res.ok) {
      setFailed(true);
      return;
    }
    announceAllowanceChanged(res.change);
  }

  const dialogs = (
    <>
      {summary && <PlanSummaryDialog plan={summary} onClose={() => setSummary(null)} />}
      {buyOpen && <BuyDialog data={data} onClose={() => setBuyOpen(false)} />}
    </>
  );

  if (!apps.has_plan) {
    return (
      <section data-testid="plan-card" className="rounded-xl bg-white p-6 shadow-soft">
        <h2 className="font-heading text-xl font-bold text-on-surface">{t("cardTitle")}</h2>
        <p className="mt-1 text-[14px] text-on-surface-variant">{t("testLine")}</p>
        <PlanTiles data={data} onChoose={setSummary} />
        {dialogs}
      </section>
    );
  }

  return (
    <section data-testid="plan-card" className="rounded-xl bg-white p-6 shadow-soft">
      <h2 className="font-heading text-xl font-bold text-on-surface">{t("cardTitle")}</h2>
      <p className="mt-1 text-[14px] font-semibold text-on-surface" data-testid="plan-line">
        {t("planLine", { plan: data.plan?.label ?? "", count: data.plan?.applications ?? apps.limit })}
      </p>
      <p className="text-[13px] text-on-surface-variant">{t("testNote")}</p>

      <div className="mt-4 text-[14px] text-on-surface" data-testid="plan-apps-meter">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span className="font-semibold">{t("appsLeft", { left: apps.remaining, limit: apps.limit })}</span>
          <span className="text-[13px] text-on-surface-variant">{t("renewsOn", { date: renews })}</span>
        </div>
        <Meter used={apps.used} limit={apps.limit} testId="plan-apps-bar" />
        {apps.purchased_remaining > 0 && (
          <div className="mt-2 flex flex-wrap items-baseline justify-between gap-x-3" data-testid="plan-purchased">
            <span className="font-semibold">{t("boughtLine", { count: apps.purchased_remaining })}</span>
            <span className="text-[13px] text-on-surface-variant">{t("doNotExpire")}</span>
          </div>
        )}
      </div>

      {data.imports && (
        <div className="mt-4 text-[14px] text-on-surface" data-testid="plan-imports-meter">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <span className="font-semibold">{t("importsLeft", { left: data.imports.remaining, limit: data.imports.limit })}</span>
            <span className="text-[13px] text-on-surface-variant">{t("renewsOn", { date: renews })}</span>
          </div>
          <Meter used={data.imports.used} limit={data.imports.limit} testId="plan-imports-bar" />
        </div>
      )}

      {data.pending && (
        <p data-testid="plan-pending" className="mt-4 rounded-lg border border-warning/40 bg-warning-container px-3 py-2.5 text-[13px] text-on-surface">
          {t("pendingLine", { date: formatDay(data.pending.from, locale), plan: data.pending.label, count: data.pending.applications })}
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {up && (
          <ActionButton data-testid="plan-upgrade" onClick={() => setSummary(up)}>
            {t("switchTo", { plan: up.label })}
          </ActionButton>
        )}
        {!up && data.pending && (
          <ActionButton data-testid="plan-stay" disabled={busy || !data.plan} onClick={() => data.plan && void changeDirect(data.plan.id)}>
            {t("stayWith", { plan: data.plan?.label ?? "" })}
          </ActionButton>
        )}
        {!up && !data.pending && down && (
          <ActionButton data-testid="plan-downgrade" disabled={busy} onClick={() => void changeDirect(down.id)}>
            {t("downgradeFrom", { month: formatMonthOf(data.renews_on, locale), plan: down.label })}
          </ActionButton>
        )}
        <ActionButton data-testid="plan-buy" onClick={() => setBuyOpen(true)}>
          {t("buy")}
        </ActionButton>
      </div>
      {failed && <DialogError testId="plan-error">{t("error")}</DialogError>}

      <p className="mt-4 text-[12.5px] leading-snug text-on-surface-variant">{t("explain")}</p>
      {dialogs}
    </section>
  );
}
