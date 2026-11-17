"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Sorbet WP-Q10 (ruling Q7-2; Q10-1…Q10-3) — "One more question before your CV
 * is written". The posting's terms the person never used, each with the words
 * that made Applire count it as covered. Nothing is ticked at the start; every
 * term is shown. The CV button of the screen that hosts the card applies the
 * ticks (`selected`), and nothing ticked is a valid answer. Copy: COPY.md of
 * WP-Q10, from the approved mock (tick-list.html v3).
 */

import { useState } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { answerPostingTerms, type PostingTerm, type PostingTermsState } from "@/lib/posting-terms";

interface Props {
  terms: PostingTerm[];
  selected: string[];
  onChange: (selected: string[]) => void;
  /** The document's language — the quote marks of the person's words follow it. */
  language?: string | null;
  error?: string | null;
  disabled?: boolean;
}

export function PostingTermsCard({ terms, selected, onChange, language, error, disabled }: Props) {
  const t = useTranslations("interview.postingTerms");
  if (terms.length === 0) return null;
  const [open, close] = language === "de" ? ["„", "“"] : ["“", "”"];

  function toggle(term: string) {
    onChange(selected.includes(term) ? selected.filter((x) => x !== term) : [...selected, term]);
  }

  return (
    <section
      data-testid="posting-terms-card"
      className="w-full rounded-lg border border-outline-variant bg-white p-4 text-left"
    >
      <h3 className="mb-2 text-[15px] font-bold text-on-surface">{t("title")}</h3>
      <p className="mb-3 text-sm text-on-surface-variant">{t("intro")}</p>
      <ul className="my-3 flex flex-col gap-2">
        {terms.map((item) => {
          const on = selected.includes(item.term);
          return (
            <li key={item.term}>
              <label
                data-testid="posting-term"
                data-term={item.term}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-[10px] border bg-white px-3.5 py-3",
                  on ? "border-2 border-teal bg-surface-container px-[13px] py-[11px]" : "border-outline-variant",
                  disabled && "cursor-not-allowed opacity-60",
                )}
              >
                <input
                  type="checkbox"
                  data-testid="posting-term-tick"
                  className="mt-0.5 h-[18px] w-[18px] flex-none accent-teal"
                  checked={on}
                  disabled={disabled}
                  onChange={() => toggle(item.term)}
                />
                <span className="min-w-0">
                  <b className="block text-[14.5px] text-on-surface">{item.term}</b>
                  {item.their_words.length > 0 && (
                    <>
                      <span className="mt-1.5 block text-[11.5px] font-bold uppercase tracking-[.04em] text-on-surface-variant">
                        {t("yourWords")}
                      </span>
                      {item.their_words.map((w) => (
                        <span key={w} data-testid="posting-term-words" className="block break-words text-[13.5px] text-on-surface">
                          {open}
                          {w}
                          {close}
                        </span>
                      ))}
                    </>
                  )}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
      <p className="text-[12.5px] text-on-surface-variant">{t("footnote")}</p>
      {error && (
        <p role="alert" data-testid="posting-terms-error" className="mt-2 text-sm text-critical">
          {error}
        </p>
      )}
    </section>
  );
}

export function PostingTermsReceipt({ confirmed, onDismiss }: { confirmed: string[]; onDismiss?: () => void }) {
  const t = useTranslations("interview.postingTerms");
  const tCommon = useTranslations("common");
  if (confirmed.length === 0) return null;
  return (
    <div
      role="status"
      data-testid="posting-terms-receipt"
      className="flex items-start gap-2 rounded-lg border border-success/40 bg-success-container px-3 py-2.5 text-sm text-on-surface"
    >
      <span className="flex-1">{t("receipt", { count: confirmed.length, terms: confirmed.join(", ") })}</span>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={tCommon("close")}
          data-testid="posting-terms-receipt-dismiss"
          className="text-on-surface-variant hover:text-on-surface"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

/**
 * The same card before a generation that does not start on the completion
 * screen (the CV page's generate and regenerate, a re-tailor that lands there):
 * the ticks are stored, then `onAnswered` runs the generation the person asked
 * for. Cancel generates nothing.
 */
export function PostingTermsGate({
  jobId,
  state,
  onAnswered,
  onCancel,
}: {
  jobId: string;
  state: PostingTermsState;
  onAnswered: (answered: PostingTermsState) => void;
  onCancel: () => void;
}) {
  const t = useTranslations("interview");
  const tCommon = useTranslations("common");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (typeof document === "undefined") return null;

  async function apply() {
    setBusy(true);
    try {
      const answered = await answerPostingTerms(jobId, selected);
      onAnswered(answered);
    } catch {
      setError(t("postingTerms.saveFailed"));
      setBusy(false);
    }
  }

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("postingTerms.title")}
      data-testid="posting-terms-gate"
      className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
    >
      <div className="max-h-[92vh] w-full overflow-y-auto rounded-t-2xl bg-white p-4 shadow-xl md:max-w-lg md:rounded-xl md:p-6">
        <PostingTermsCard
          terms={state.terms}
          selected={selected}
          onChange={setSelected}
          language={state.language}
          error={error}
          disabled={busy}
        />
        <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            data-testid="posting-terms-gate-cancel"
            className="rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container"
          >
            {tCommon("cancel")}
          </button>
          <button
            type="button"
            onClick={() => void apply()}
            disabled={busy}
            data-testid="posting-terms-gate-generate"
            className="rounded-lg bg-primary px-4 py-2 text-[13px] font-bold text-white hover:opacity-90 disabled:opacity-60"
          >
            {t("generateCV")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
