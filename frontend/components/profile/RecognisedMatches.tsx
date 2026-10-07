// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * #717 — the import summary's "Schon in deinem Profil" card (ADR-063 amended
 * 2026-10-07; mock gate V-3, ruling V-2 = A).
 *
 * Lists the merge's `matched` receipts — entries the import recognised as
 * already in the profile under another name — as `incoming → existing` pairs,
 * each with a link to its section and a one-click "Nicht dasselbe" that calls
 * `POST /api/profile/matches/separate` (adds the entry as its own, removes the
 * alternate name the match recorded; both steps in the history). A pair the
 * closed DE/EN language-name table recognised is a fact and gets a tag instead
 * of the button. Folds after five rows.
 */
import { useState } from "react";
import { useTranslations } from "next-intl";
import { ArrowRight, Check } from "lucide-react";
import type { MatchReceiptItem } from "@/lib/import-cv";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const FOLD_AFTER = 5;

type RowState = "idle" | "busy" | "done" | "failed";

export function RecognisedMatches({
  matched,
  onSeparated,
}: {
  matched: MatchReceiptItem[];
  onSeparated?: () => void;
}) {
  const t = useTranslations("importSummary");
  const tSection = useTranslations("review.section");
  const [expanded, setExpanded] = useState(false);
  const [rows, setRows] = useState<Record<number, RowState>>({});

  const items = matched.filter((m) => !m.undone_at);
  if (items.length === 0) return null;
  const visible = expanded ? items : items.slice(0, FOLD_AFTER);

  function sectionLabel(section: string): string {
    return tSection.has(section) ? tSection(section) : tSection("*");
  }

  async function separate(index: number, item: MatchReceiptItem) {
    setRows((r) => ({ ...r, [index]: "busy" }));
    try {
      const res = await fetch(`${API_BASE}/api/profile/matches/separate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_id: item.entity_id, incoming: item.incoming }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setRows((r) => ({ ...r, [index]: "done" }));
      onSeparated?.();
    } catch {
      setRows((r) => ({ ...r, [index]: "failed" }));
    }
  }

  return (
    <div
      data-testid="recognised-matches"
      className="mt-3 rounded-[10px] border border-outline-variant bg-primary-container px-4 py-3.5"
    >
      <h3 className="font-manrope text-[14px] font-bold text-primary">
        {t("recognisedTitle", { count: items.length })}
      </h3>
      <p className="mt-1 mb-2.5 text-[12px] leading-relaxed text-on-surface-variant">
        {t("recognisedIntro")}
      </p>
      <ul>
        {visible.map((item, index) => {
          const state = rows[index] ?? "idle";
          const undoable = item.basis !== "name_table";
          return (
            <li
              key={`${item.entity_id}-${item.incoming}`}
              data-testid="recognised-match-row"
              className="flex flex-wrap items-center gap-2 border-t border-outline-variant py-2 text-[13px]"
            >
              <span className="min-w-[96px] text-[11px] text-on-surface-variant">
                {sectionLabel(item.section)}
              </span>
              <span
                className={
                  "flex min-w-[180px] flex-1 items-center gap-1 text-on-surface" +
                  (state === "done" ? " line-through text-on-surface-variant" : "")
                }
              >
                <span className="font-semibold">{item.incoming}</span>
                <ArrowRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
                <span>{item.existing}</span>
              </span>
              {state === "done" ? (
                <span
                  data-testid="recognised-match-done"
                  className="inline-flex items-center gap-1 text-[12px] text-[#166534]"
                >
                  <Check aria-hidden="true" className="h-3.5 w-3.5" />
                  {t("separatedDone")}
                </span>
              ) : (
                <>
                  <a
                    href={`/profile#section-${item.section}`}
                    className="text-[12px] text-primary underline"
                  >
                    {t("showInProfile")}
                  </a>
                  {undoable ? (
                    <button
                      type="button"
                      data-testid="recognised-match-separate"
                      disabled={state === "busy"}
                      onClick={() => void separate(index, item)}
                      className="rounded-full border border-outline-variant bg-white px-2.5 py-1 text-[12px] text-on-surface hover:bg-surface-container disabled:opacity-60"
                    >
                      {t("notSame")}
                    </button>
                  ) : (
                    <span
                      data-testid="recognised-match-table"
                      className="rounded-full border border-dashed border-outline-variant px-2 py-0.5 text-[11px] text-on-surface-variant"
                    >
                      {t("sameLanguage")}
                    </span>
                  )}
                  {state === "failed" && (
                    <span role="alert" className="text-[12px] text-red-700">
                      {t("separateFailed")}
                    </span>
                  )}
                </>
              )}
            </li>
          );
        })}
      </ul>
      {items.length > FOLD_AFTER && !expanded && (
        <button
          type="button"
          data-testid="recognised-matches-show-all"
          onClick={() => setExpanded(true)}
          className="mt-1.5 text-[12px] text-primary"
        >
          {t("showAll", { count: items.length })}
        </button>
      )}
    </div>
  );
}
