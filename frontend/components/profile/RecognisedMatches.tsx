// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * #717 — the import summary's recognised pairs (ADR-063 amended 2026-10-07;
 * mock gate V-3, ruling V-2 = A), reshaped by FS-6 (Hazelnut build 4, ruling
 * V-4 = A, mock V-2): each pair the import combined with an existing entry is
 * ONE question — "Ist A dasselbe wie B?" — with "Ja" (`POST
 * /api/profile/matches/confirm`: never asked again) and "Nein, getrennt führen"
 * (the existing undo, `POST /api/profile/matches/separate`). A pair the closed
 * DE/EN language-name table recognised is a fact and stays a note. Folds after
 * five rows. Bindings the import REFUSED (#801) are listed below as "Separat
 * behalten". Used on the import card and on My Profile's import summary.
 */
import { useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { ArrowRight, Check } from "lucide-react";
import type { KeptSeparateItem, MatchReceiptItem } from "@/lib/import-cv";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const FOLD_AFTER = 5;

type RowState = "idle" | "busy" | "same" | "separated" | "failed";

function sameWords(a: string, b: string): boolean {
  const norm = (v: string) => v.normalize("NFC").trim().toLocaleLowerCase().split(/\s+/).join(" ");
  return norm(a) === norm(b);
}

export function RecognisedMatches({
  matched,
  keptSeparate = [],
  onSeparated,
}: {
  matched: MatchReceiptItem[];
  keptSeparate?: KeptSeparateItem[];
  onSeparated?: () => void;
}) {
  const t = useTranslations("importSummary");
  const tSection = useTranslations("review.section");
  const [expanded, setExpanded] = useState(false);
  const [rows, setRows] = useState<Record<string, RowState>>({});

  // E2E-3 (b): an identical pair is no rename — never a row, never an undo
  // (receipts persisted before the backend stopped writing them).
  const items = matched.filter((m) => !m.undone_at && !sameWords(m.incoming, m.existing));
  const kept = keptSeparate.filter((k) => k.incoming && k.existing);
  if (items.length === 0 && kept.length === 0) return null;
  const visible = expanded ? items : items.slice(0, FOLD_AFTER);

  function sectionLabel(section: string): string {
    return tSection.has(section) ? tSection(section) : tSection("*");
  }

  async function answer(key: string, item: MatchReceiptItem, same: boolean) {
    setRows((r) => ({ ...r, [key]: "busy" }));
    try {
      const init: RequestInit = {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_id: item.entity_id, incoming: item.incoming }),
      };
      const res = same
        ? await fetch(`${API_BASE}/api/profile/matches/confirm`, { ...init, method: "POST" })
        : await fetch(`${API_BASE}/api/profile/matches/separate`, { ...init, method: "POST" });
      if (!res.ok) throw new Error(String(res.status));
      setRows((r) => ({ ...r, [key]: same ? "same" : "separated" }));
      if (!same) onSeparated?.();
    } catch {
      setRows((r) => ({ ...r, [key]: "failed" }));
    }
  }

  const bold = (chunks: ReactNode) => <b className="font-semibold">{chunks}</b>;

  return (
    <div
      data-testid="recognised-matches"
      className="mt-3 rounded-[10px] border border-outline-variant bg-primary-container px-4 py-3.5"
    >
      {items.length > 0 && (
        <>
          <h3 className="font-manrope text-[14px] font-bold text-primary">
            {t("combinedTitle", { count: items.length })}
          </h3>
          <p className="mt-1 mb-2.5 text-[12px] leading-relaxed text-on-surface-variant">
            {t("combinedIntro")}
          </p>
          <ul>
            {visible.map((item) => {
              const key = `${item.entity_id}-${item.incoming}`;
              const state = rows[key] ?? (item.confirmed_at ? "same" : "idle");
              const fact = item.basis === "name_table";
              return (
                <li
                  key={key}
                  data-testid="recognised-match-row"
                  className="flex flex-wrap items-center gap-2 border-t border-outline-variant py-2 text-[13px]"
                >
                  <span className="basis-full text-[11px] text-on-surface-variant sm:basis-auto sm:min-w-[96px]">
                    {sectionLabel(item.section)}
                  </span>
                  {fact ? (
                    <>
                      <span className="flex min-w-0 basis-full flex-wrap items-center gap-x-1 break-words text-on-surface sm:basis-auto sm:flex-1">
                        <span className="font-semibold">{item.incoming}</span>
                        <ArrowRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
                        <span>{item.existing}</span>
                      </span>
                      <span
                        data-testid="recognised-match-table"
                        className="rounded-full border border-dashed border-outline-variant px-2 py-0.5 text-[11px] text-on-surface-variant"
                      >
                        {t("sameLanguage")}
                      </span>
                    </>
                  ) : (
                    <>
                      <span
                        data-testid="recognised-match-question"
                        className={
                          "min-w-0 basis-full break-words text-on-surface sm:basis-auto sm:flex-1" +
                          (state === "separated" ? " line-through text-on-surface-variant" : "")
                        }
                      >
                        {t.rich("pairQuestion", { incoming: item.incoming, existing: item.existing, b: bold })}
                      </span>
                      {state === "same" || state === "separated" ? (
                        <span
                          data-testid={state === "same" ? "recognised-match-same" : "recognised-match-done"}
                          className="inline-flex items-center gap-1 text-[12px] text-[#166534]"
                        >
                          <Check aria-hidden="true" className="h-3.5 w-3.5" />
                          {state === "same" ? t("pairConfirmed") : t("separatedDone")}
                        </span>
                      ) : (
                        <span className="flex flex-wrap items-center gap-1.5">
                          <button
                            type="button"
                            data-testid="recognised-match-yes"
                            disabled={state === "busy"}
                            onClick={() => void answer(key, item, true)}
                            className="rounded-full border border-outline-variant bg-white px-2.5 py-1 text-[12px] text-on-surface hover:bg-surface-container disabled:opacity-60"
                          >
                            {t("pairYes")}
                          </button>
                          <button
                            type="button"
                            data-testid="recognised-match-separate"
                            disabled={state === "busy"}
                            onClick={() => void answer(key, item, false)}
                            className="rounded-full border border-outline-variant bg-white px-2.5 py-1 text-[12px] text-on-surface hover:bg-surface-container disabled:opacity-60"
                          >
                            {t("pairNo")}
                          </button>
                          {state === "failed" && (
                            <span role="alert" className="text-[12px] text-red-700">
                              {t("separateFailed")}
                            </span>
                          )}
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
        </>
      )}
      {kept.length > 0 && (
        <div data-testid="kept-separately" className={items.length > 0 ? "mt-3 border-t border-outline-variant pt-3" : ""}>
          <h3 className="font-manrope text-[14px] font-bold text-primary">
            {t("keptSeparatelyTitle", { count: kept.length })}
          </h3>
          <ul className="mt-1.5 space-y-1">
            {kept.map((k) => (
              <li key={`${k.entity_id}-${k.incoming}`} data-testid="kept-separately-row" className="text-[13px] text-on-surface">
                {t.rich("keptSeparately", { incoming: k.incoming, existing: k.existing, b: bold })}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
