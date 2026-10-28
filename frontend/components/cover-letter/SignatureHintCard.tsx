"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";

/**
 * ADR-099 cl. 9 (UAT-3g, RULING C-1 (3)): the letter's signature hint is ONE
 * counted class-A card in the letter's review list — "Im Profil hinzufügen" or
 * "Ohne Unterschrift" (saved as the letter kind default `signature_in_letter =
 * false`, so the hint never comes back). The CV never shows it.
 */
export function SignatureHintCard({
  onWithout,
  saving,
  failed,
}: {
  onWithout: () => void;
  saving: boolean;
  failed: boolean;
}) {
  const t = useTranslations("documentReview.delivery");
  const router = useRouter();
  const btn =
    "flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl border border-outline-variant bg-surface-bright px-3 text-sm font-semibold text-on-surface disabled:opacity-50";
  return (
    <div data-testid="review-signature-card" className="flex flex-col gap-2 rounded-xl border border-outline-variant p-3">
      <h4 className="font-heading text-[14.5px] font-bold text-on-surface">{t("signatureTitle")}</h4>
      <div className="flex flex-wrap gap-2">
        <button type="button" data-testid="review-signature-add" onClick={() => router.push("/profile")} className={btn}>
          {t("signatureAdd")}
        </button>
        <button type="button" data-testid="review-signature-without" disabled={saving} onClick={onWithout} className={btn}>
          {saving && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
          {t("signatureWithout")}
        </button>
      </div>
      <p className="text-xs text-on-surface-variant">{t("signatureNote")}</p>
      {failed && (
        <p role="alert" data-testid="review-signature-error" className="text-[13px] text-critical">
          {t("signatureSaveError")}
        </p>
      )}
    </div>
  );
}
