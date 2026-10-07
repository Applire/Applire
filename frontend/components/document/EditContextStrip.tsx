"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

// #737 (Strawberry build 2, WP-E) — the strip at the top of *Bearbeiten* that
// says WHY the user is here when a review handle sent him (ADR-090 cl. 5 *Ich
// bearbeite es selbst*, ADR-081 cl. 3 am. 09-05 group-2 row, #702 *In den
// Lebenslauf übernehmen*), and what the save did. Presentation only: it names
// what the producers already said and never decides a finding's state itself —
// the "still flagged" bit is read from the refreshed report by the page.
import { ArrowLeft, Check, CircleAlert, X } from "lucide-react";
import { useTranslations } from "next-intl";

export type EditContext =
  | { kind: "finding"; label: string; form: string }
  | { kind: "gap"; label: string }
  | { kind: "letter"; facts: string[] };

export type SaveReceipt =
  | { kind: "plain" }
  | { kind: "finding"; label: string; stillListed: boolean; openCount: number };

interface EditContextStripProps {
  context: EditContext | null;
  receipt: SaveReceipt | null;
  /** Back to where the context came from (the review tab, or the letter). */
  onBack?: () => void;
  onDismiss?: () => void;
}

export function EditContextStrip({ context, receipt, onBack, onDismiss }: EditContextStripProps) {
  const t = useTranslations("editTab");
  if (!context && !receipt) return null;
  const letter = context?.kind === "letter";
  return (
    <div className="flex flex-col gap-2 px-3 pt-3" data-testid="edit-context">
      {context && (
        <div
          data-testid={`edit-context-${context.kind}`}
          className={`flex flex-col gap-1.5 rounded-xl border px-3 py-2.5 ${
            letter ? "border-warning bg-warning-container" : "border-outline-variant bg-primary-container"
          }`}
        >
          <div className="flex items-start gap-2">
            <span
              className={`flex-1 text-[10px] font-bold uppercase tracking-wider ${letter ? "text-gold-dim" : "text-primary"}`}
            >
              {letter ? t("fromLetterKicker") : t("fromReviewKicker")}
              {context.kind !== "letter" && (
                <>
                  {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- separator glyph */}
                  <span aria-hidden="true">{" · "}</span>
                  <span className="normal-case tracking-normal" data-testid="edit-context-label">{context.label}</span>
                </>
              )}
            </span>
            {onDismiss && (
              <button
                type="button"
                onClick={onDismiss}
                aria-label={t("dismissContext")}
                title={t("dismissContext")}
                data-testid="edit-context-dismiss"
                className="-mr-1 -mt-0.5 rounded p-0.5 text-on-surface-variant hover:text-on-surface"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            )}
          </div>
          <p className="text-[13px] leading-snug text-on-surface">
            {context.kind === "finding"
              ? t("fromFindingBody", { form: context.form })
              : context.kind === "gap"
                ? t("fromGapBody", { label: context.label })
                : t("fromLetterBody")}
          </p>
          {context.kind === "letter" && context.facts.length > 0 && (
            <ul className="flex flex-wrap gap-1" data-testid="edit-context-facts">
              {context.facts.map((f) => (
                <li key={f} className="rounded-full border border-outline-variant bg-white px-2 py-0.5 text-xs text-on-surface">
                  {f}
                </li>
              ))}
            </ul>
          )}
          {/* After a finding save the receipt carries the way back — once. */}
          {onBack && !(receipt?.kind === "finding" && !letter) && (
            <button
              type="button"
              onClick={onBack}
              data-testid="edit-context-back"
              className="inline-flex items-center gap-1 self-start text-[12.5px] font-semibold text-primary underline"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
              {letter ? t("backToLetter") : t("backToReview")}
            </button>
          )}
        </div>
      )}
      {receipt && (
        <p
          role="status"
          data-testid={`edit-receipt-${receipt.kind === "plain" ? "plain" : receipt.stillListed ? "still" : "cleared"}`}
          className={`flex items-start gap-2 rounded-lg px-3 py-2 text-[12.5px] leading-snug text-on-surface ${
            receipt.kind === "finding" && receipt.stillListed ? "bg-warning-container" : "bg-success-container"
          }`}
        >
          {receipt.kind === "finding" && receipt.stillListed ? (
            <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gold-dim" aria-hidden="true" />
          ) : (
            <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" aria-hidden="true" />
          )}
          <span>
            {receipt.kind === "plain" ? (
              t("savedPreview")
            ) : (
              <>
                {receipt.stillListed
                  ? t("savedFindingStill", { label: receipt.label })
                  : t("savedFindingCleared", { label: receipt.label })}
                <span className="ml-1">{t("savedOpenCount", { count: receipt.openCount })}</span>
              </>
            )}
            {receipt.kind === "finding" && onBack && !letter && (
              <>
                <button type="button" onClick={onBack} className="ml-1 font-semibold text-primary underline" data-testid="edit-receipt-back">
                  {t("backToReview")}
                </button>
              </>
            )}
          </span>
        </p>
      )}
    </div>
  );
}
