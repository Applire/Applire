"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The shell's one mount for the tester-plan dialogs. It listens for a refusal of
 * the allowance (raised by the global fetch patch) and for `offerPlanChoice()`,
 * and renders the right dialog. The refused action is never retried; the person
 * presses it again.
 */

import { useEffect, useState } from "react";

import type { CatalogPlan } from "./api";
import { BuyDialog } from "./BuyDialog";
import { ChoosePlanDialog } from "./ChoosePlanDialog";
import { DailyLimitDialog, ImportsZeroDialog, UnavailableDialog, ZeroDialog } from "./AllowanceDialogs";
import { ALLOWANCE_REFUSED_EVENT, PLAN_OFFER_EVENT, type AllowanceRefusalDetail } from "./events";
import { PlanSummaryDialog } from "./PlanSummaryDialog";
import { useAllowance } from "./useAllowance";

type View = "choose" | "zero" | "imports" | "daily" | "unavailable" | "buy" | null;

/** "Später" holds the offer back for the rest of this page session (plan_required still shows). */
let offerDeclined = false;

/** Test seam. */
export function resetPlanOfferForTests(): void {
  offerDeclined = false;
}

export function TestPlanNotices() {
  const { data } = useAllowance();
  const [view, setView] = useState<View>(null);
  const [renewsOn, setRenewsOn] = useState<string | null>(null);
  const [summary, setSummary] = useState<CatalogPlan | null>(null);

  const offerable = !!data?.purchasing_available && !!data.applications && !data.applications.has_plan;

  useEffect(() => {
    const onRefused = (e: Event) => {
      const d = (e as CustomEvent<AllowanceRefusalDetail>).detail;
      if (!d) return;
      setRenewsOn(d.renews_on);
      setSummary(null);
      if (d.code === "plan_required") setView("choose");
      else if (d.code === "allowance_exhausted") setView(d.action === "profile_import" ? "imports" : "zero");
      else if (d.code === "instance_daily_limit") setView("daily");
      else if (d.code === "allowance_unavailable") setView("unavailable");
    };
    const onOffer = () => {
      if (offerable && !offerDeclined) setView("choose");
    };
    window.addEventListener(ALLOWANCE_REFUSED_EVENT, onRefused);
    window.addEventListener(PLAN_OFFER_EVENT, onOffer);
    return () => {
      window.removeEventListener(ALLOWANCE_REFUSED_EVENT, onRefused);
      window.removeEventListener(PLAN_OFFER_EVENT, onOffer);
    };
  }, [offerable]);

  const close = () => setView(null);
  const later = () => {
    offerDeclined = true;
    close();
  };

  // The refusals that need no counters render without allowance data (a refusal
  // proves the allowance is on); the others render nothing when there is none.
  return (
    <>
      {view === "daily" && <DailyLimitDialog onClose={close} />}
      {view === "unavailable" && <UnavailableDialog onClose={close} />}
      {data && view === "choose" && !summary && (
        <ChoosePlanDialog
          data={data}
          onLater={later}
          onChoose={setSummary}
        />
      )}
      {data && summary && (
        <PlanSummaryDialog
          plan={summary}
          // Back / Esc return to the dialog the choice came from; a finished change ends the flow.
          onClose={() => setSummary(null)}
          onDone={close}
        />
      )}
      {data && view === "zero" && !summary && (
        <ZeroDialog
          data={data}
          renewsOn={renewsOn}
          onClose={close}
          onBuy={() => setView("buy")}
          onUpgrade={setSummary}
        />
      )}
      {data && view === "imports" && <ImportsZeroDialog data={data} renewsOn={renewsOn} onClose={close} />}
      {data && view === "buy" && <BuyDialog data={data} onClose={close} />}
    </>
  );
}
