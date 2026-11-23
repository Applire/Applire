"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/** Boards A5 → A6 — buy a pack of applications (simulated; nothing is charged). */

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { ActionButton, Dialog, DialogError } from "@/components/account/Dialog";
import { newIdempotencyKey, postPurchase, type Allowance } from "./api";
import { announceAllowanceChanged } from "./events";
import { formatPrice } from "./format";

/** Mounted = open: the idempotency key is made once per opening. */
export function BuyDialog({ data, onClose }: { data: Allowance; onClose: () => void }) {
  const t = useTranslations("plan");
  const locale = useLocale();
  const packs = data.catalog?.packs ?? [];
  const [key] = useState(newIdempotencyKey);
  const [step, setStep] = useState<1 | 2>(1);
  const [packId, setPackId] = useState<string | null>(packs[0]?.id ?? null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const pack = packs.find((p) => p.id === packId) ?? null;

  if (!data.purchasing_available || !pack) return null;

  async function confirm() {
    if (!pack) return;
    setBusy(true);
    setFailed(false);
    const ok = await postPurchase(pack.id, key);
    setBusy(false);
    if (!ok) {
      setFailed(true);
      return;
    }
    announceAllowanceChanged("purchased");
    onClose();
  }

  if (step === 1) {
    return (
      <Dialog
        open
        onClose={onClose}
        title={t("buy")}
        testId="buy-dialog"
        actions={
          <>
            <ActionButton data-testid="buy-cancel" onClick={onClose}>
              {t("cancel")}
            </ActionButton>
            <ActionButton tone="primary" data-testid="buy-next" onClick={() => setStep(2)}>
              {t("next")}
            </ActionButton>
          </>
        }
      >
        <p>{t("buyTestLine")}</p>
        <div role="radiogroup" className="mt-4 grid gap-3 sm:grid-cols-2">
          {packs.map((p) => (
            <button
              key={p.id}
              type="button"
              role="radio"
              aria-checked={p.id === packId}
              data-testid={`buy-pack-${p.id}`}
              onClick={() => setPackId(p.id)}
              className={`flex flex-col gap-1 rounded-xl border bg-white p-4 text-left ${
                p.id === packId ? "border-teal ring-2 ring-teal/30" : "border-outline-variant"
              }`}
            >
              <span className="text-[15px] font-bold text-on-surface">{t("packTitle", { count: p.applications })}</span>
              <span className="text-[13px]">{t("doNotExpire")}</span>
              <span className="font-semibold text-on-surface">{formatPrice(p.price_cents, locale)}</span>
            </button>
          ))}
        </div>
        <p className="mt-4 text-[13px]">{t("buyExplain")}</p>
      </Dialog>
    );
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("summaryTitle")}
      testId="buy-summary-dialog"
      actions={
        <>
          <ActionButton data-testid="buy-back" onClick={() => setStep(1)}>
            {t("back")}
          </ActionButton>
          <ActionButton tone="primary" data-testid="buy-confirm" disabled={busy} onClick={() => void confirm()}>
            {t("completePurchase")}
          </ActionButton>
        </>
      }
    >
      <p>{t("buySummaryLine")}</p>
      <div className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2">
        <span className="font-semibold text-on-surface">{t("packTitle", { count: pack.applications })}</span>
        <span className="font-semibold text-on-surface">{formatPrice(pack.price_cents, locale)}</span>
        <span>{t("paymentLabel")}</span>
        <span className="text-on-surface">{t("paymentNone")}</span>
        <span>{t("dueLabel")}</span>
        <span data-testid="buy-due" className="font-semibold text-on-surface">
          {formatPrice(0, locale)}
        </span>
      </div>
      <p className="mt-4 text-[13px]">{t("buyAvailable")}</p>
      {failed && <DialogError testId="buy-error">{t("error")}</DialogError>}
    </Dialog>
  );
}
