// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * D-B3V6 (ADR-046 + ADR-063 amended 2026-10-22; ruling V-3 = A) — the import
 * summary's "Schon in deinem Profil (anders formuliert)" card.
 *
 * Lists the bullets a second source brought that the import's restatement
 * check held back because they say what a bullet of the same role already
 * says. Nothing was written or deleted; each row shows the new wording, the
 * held bullet it restates, and a one-click "Trotzdem übernehmen" that writes
 * it on the candidate's word (`POST /api/profile/restated/apply`). Folds after
 * five rows — the same shape as `RecognisedMatches`.
 */
import { useState } from "react";
import { useTranslations } from "next-intl";
import { Check } from "lucide-react";
import type { RestatedReceiptItem } from "@/lib/import-cv";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const FOLD_AFTER = 5;

type RowState = "idle" | "busy" | "done" | "failed";

export function RestatedBullets({
  restated,
  onApplied,
}: {
  restated: RestatedReceiptItem[];
  onApplied?: () => void;
}) {
  const t = useTranslations("importSummary");
  const [expanded, setExpanded] = useState(false);
  const [rows, setRows] = useState<Record<number, RowState>>({});

  if (restated.length === 0) return null;
  const visible = expanded ? restated : restated.slice(0, FOLD_AFTER);

  async function apply(index: number, item: RestatedReceiptItem) {
    setRows((r) => ({ ...r, [index]: "busy" }));
    try {
      const res = await fetch(`${API_BASE}/api/profile/restated/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_id: item.entity_id, field: item.field, text: item.text }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setRows((r) => ({ ...r, [index]: "done" }));
      onApplied?.();
    } catch {
      setRows((r) => ({ ...r, [index]: "failed" }));
    }
  }

  return (
    <div
      data-testid="restated-bullets"
      className="mt-3 rounded-[10px] border border-outline-variant bg-white px-4 py-3.5"
    >
      <h3 className="font-manrope text-[14px] font-bold text-on-surface">
        {t("restatedTitle", { count: restated.length })}
      </h3>
      <p className="mt-1 mb-2.5 text-[12px] leading-relaxed text-on-surface-variant">
        {t("restatedIntro")}
      </p>
      <ul>
        {visible.map((item, index) => {
          const state = rows[index] ?? "idle";
          return (
            <li
              key={`${item.entity_id}-${item.field}-${item.text}`}
              data-testid="restated-row"
              className="flex flex-col gap-1 border-t border-outline-variant py-2 text-[13px]"
            >
              {item.entity_label && (
                <span className="text-[11px] text-on-surface-variant">
                  {item.entity_label.replace(" @ ", " – ")}
                </span>
              )}
              <span className="break-words font-semibold text-on-surface">{item.text}</span>
              <span className="break-words text-[12px] text-on-surface-variant">
                {t("restatedHeld", { text: item.restates })}
              </span>
              <span className="flex flex-wrap items-center gap-2">
                {state === "done" ? (
                  <span
                    data-testid="restated-done"
                    className="inline-flex items-center gap-1 text-[12px] text-[#166534]"
                  >
                    <Check aria-hidden="true" className="h-3.5 w-3.5" />
                    {t("restatedApplied")}
                  </span>
                ) : (
                  <button
                    type="button"
                    data-testid="restated-apply"
                    disabled={state === "busy"}
                    onClick={() => void apply(index, item)}
                    className="rounded-full border border-outline-variant bg-white px-2.5 py-1 text-[12px] text-on-surface hover:bg-surface-container disabled:opacity-60"
                  >
                    {t("restatedApply")}
                  </button>
                )}
                {state === "failed" && (
                  <span role="alert" className="text-[12px] text-red-700">
                    {t("separateFailed")}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {restated.length > FOLD_AFTER && !expanded && (
        <button
          type="button"
          data-testid="restated-show-all"
          onClick={() => setExpanded(true)}
          className="mt-1.5 text-[12px] text-primary"
        >
          {t("showAll", { count: restated.length })}
        </button>
      )}
    </div>
  );
}
