// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * #702 (ADR-060 amended 2026-10-07; RULING R-2 = A) — the cross-document section
 * of the review panel: "Gegengelesen: Anschreiben und Lebenslauf".
 *
 * One card per letter sentence the CV does not back (the backend's derived
 * `critic_report.cross_document`, high weight first), with three actions:
 * *In den Lebenslauf übernehmen* (opens the CV's Edit tab — nothing pre-filled,
 * ADR-081 cl. 3), *Aus dem Anschreiben nehmen* (the ADR-090 cl. 3 removal
 * rewrite, undoable) and *So lassen* (records `kept`). A decision LABELS an item
 * decided — the critic does not re-run after an edit. ADR-081 cl. 9: a critic
 * that did not run is stated, never rendered as an empty section.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Check, ChevronRight, Loader2 } from "lucide-react";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import { buildCrossDocument, type CrossDocumentRow } from "@/lib/review-groups";
import {
  keepCrossDocument,
  ReviewActionError,
  takeOut,
  undoDecision,
  type ReviewDocumentKind,
  type ReviewRefresh,
  type ReviewState,
} from "@/lib/api/document-review";

export interface CrossDocumentSectionProps {
  documentKind: ReviewDocumentKind;
  documentId: string | null;
  criticReport: OutcomeCriticReport;
  reviewState: ReviewState | null;
  /** Where *In den Lebenslauf übernehmen* goes. Absent → the action is not offered. */
  cvEditHref?: string | null;
  onRefresh?: (refresh: ReviewRefresh, opts: { documentChanged: boolean }) => void;
}

/**
 * The facts the CV never mentions; an older backend without `letter_only`
 * falls back to every concept. Shared with the CV page's Edit-tab context
 * strip (#737), so both surfaces name the same facts.
 */
export function letterOnlyFacts(item: { letter_only?: string[] | null; concepts: string[] }): string[] {
  return item.letter_only && item.letter_only.length > 0 ? item.letter_only : item.concepts;
}

/** Split a sentence into plain and marked runs, marking every concept that stands in it literally. */
function markRuns(sentence: string, concepts: string[]): { text: string; mark: boolean }[] {
  const hits: [number, number][] = [];
  const lower = sentence.toLowerCase();
  for (const c of concepts) {
    const i = lower.indexOf(c.toLowerCase());
    if (i >= 0) hits.push([i, i + c.length]);
  }
  hits.sort((a, b) => a[0] - b[0]);
  const runs: { text: string; mark: boolean }[] = [];
  let pos = 0;
  for (const [s, e] of hits) {
    if (s < pos) continue;
    if (s > pos) runs.push({ text: sentence.slice(pos, s), mark: false });
    runs.push({ text: sentence.slice(s, e), mark: true });
    pos = e;
  }
  if (pos < sentence.length) runs.push({ text: sentence.slice(pos), mark: false });
  return runs;
}

export function CrossDocumentSection({
  documentKind,
  documentId,
  criticReport,
  reviewState,
  cvEditHref,
  onRefresh,
}: CrossDocumentSectionProps) {
  const t = useTranslations("reviewSignals");
  const tErrors = useTranslations("errors");
  const section = useMemo(() => buildCrossDocument(criticReport, reviewState), [criticReport, reviewState]);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stillThere, setStillThere] = useState<string | null>(null);

  if (section.unknown) {
    // Only the letter has a cross-document critic; on the CV nothing renders.
    if (documentKind !== "cover-letter") return null;
    return (
      <p data-testid="review-xdoc-unknown" className="text-xs text-on-surface-variant">
        {t("unknown")}
      </p>
    );
  }
  if (section.rows.length === 0) return null;

  const firstOpen = section.rows.find((r) => r.status === "open");
  const current = section.rows.find((r) => r.item.key === openKey) ?? firstOpen ?? null;

  const fail = (e: unknown) => {
    const status = e instanceof ReviewActionError ? e.status : 0;
    setError(tErrors("generic", { status: status || "—" }));
  };

  const run = async (row: CrossDocumentRow, action: "takeout" | "keep" | "undo") => {
    if (!documentId || busy) return;
    setBusy(action);
    setError(null);
    setStillThere(null);
    try {
      if (action === "takeout") {
        const res = await takeOut(documentKind, documentId, row.item.key);
        if (res.still_listed) setStillThere(row.item.key);
        onRefresh?.(res, { documentChanged: res.changes.length > 0 });
      } else if (action === "keep") {
        const res = await keepCrossDocument(documentKind, documentId, row.item.key, true);
        onRefresh?.({ review_state: res.review_state }, { documentChanged: false });
      } else if (row.status === "kept") {
        const res = await keepCrossDocument(documentKind, documentId, row.item.key, false);
        onRefresh?.({ review_state: res.review_state }, { documentChanged: false });
      } else {
        const res = await undoDecision(documentKind, documentId, row.item.key);
        onRefresh?.(res, { documentChanged: true });
      }
      setOpenKey(row.item.key);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const STATUS_LABEL: Record<Exclude<CrossDocumentRow["status"], "open">, string> = {
    kept: t("statusKept"),
    taken_out: t("statusTakenOut"),
    edited: t("statusEdited"),
  };

  const card = (row: CrossDocumentRow) => {
    const onlyLetter = row.item.kinds.includes("letter_only");
    const high = row.item.weight === "high";
    // The facts the CV never mentions; an older backend without the field
    // falls back to every concept.
    const shown = letterOnlyFacts(row.item);
    return (
      <div
        data-testid="review-xdoc-card"
        data-weight={row.item.weight}
        className={`flex flex-col gap-2 rounded-2xl border p-3 ${
          high ? "border-warning bg-warning-container" : "border-outline-variant bg-surface-bright"
        }`}
      >
        {high && (
          <span data-testid="review-xdoc-kicker" className="text-[10px] font-bold uppercase tracking-wider text-gold-dim">
            {t("kickerHigh")}
          </span>
        )}
        <p className="font-heading text-[15px] font-bold text-on-surface">
          {onlyLetter ? t("cardTitleOnly") : t("cardTitleRicher")}
        </p>
        <p className="text-[13px] leading-snug text-on-surface">
          {onlyLetter
            ? t("cardBodyOnly", { count: shown.length, high: high ? "true" : "false" })
            : t("cardBodyRicher")}
        </p>
        <ul className="flex flex-wrap gap-1" data-testid="review-xdoc-concepts">
          {shown.map((c) => (
            <li key={c} className="rounded-full border border-outline-variant bg-white px-2 py-0.5 text-xs text-on-surface">
              {c}
            </li>
          ))}
        </ul>
        <blockquote
          data-testid="review-xdoc-quote"
          className="rounded-xl border border-outline-variant bg-white px-3 py-2 text-[13px] leading-snug text-on-surface"
        >
          <span className="block text-xs text-on-surface-variant">{t("quoteLabel")}</span>
          {markRuns(row.item.letter_state, shown).map((r, i) =>
            r.mark ? (
              <mark key={i} className="rounded bg-gold-container px-0.5 text-on-surface">
                {r.text}
              </mark>
            ) : (
              <span key={i}>{r.text}</span>
            ),
          )}
        </blockquote>

        {row.status !== "open" ? (
          <div className="flex flex-wrap items-center gap-2">
            <span
              data-testid={`review-xdoc-status-${row.status}`}
              className="flex items-center gap-1 rounded-full bg-success-container px-2 py-0.5 text-[11px] font-semibold text-success"
            >
              <Check className="h-3.5 w-3.5" aria-hidden="true" />
              {STATUS_LABEL[row.status]}
            </span>
            {row.status !== "edited" && (
              <button
                type="button"
                data-testid="review-xdoc-undo"
                disabled={busy !== null}
                onClick={() => run(row, "undo")}
                className="min-h-11 rounded-xl px-3 text-[13px] font-semibold text-primary disabled:opacity-60"
              >
                {t("actionUndo")}
              </button>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {cvEditHref && (
              <Link
                // #737: the item key rides along so the CV's Edit tab can keep
                // the letter-only facts in view (it re-reads them from the
                // letter's own critic report — no document text in the URL).
                href={`${cvEditHref}${cvEditHref.includes("?") ? "&" : "?"}xdoc=${encodeURIComponent(row.item.key)}`}
                data-testid="review-xdoc-add-to-cv"
                className="block min-h-11 rounded-xl bg-primary px-3 py-2 text-left text-[13px] font-semibold text-white"
              >
                {t("actionAddToCv")}
                <span className="block text-[11.5px] font-normal text-surface-container-highest">
                  {t("actionAddToCvHint")}
                </span>
              </Link>
            )}
            <button
              type="button"
              data-testid="review-xdoc-take-out"
              disabled={busy !== null}
              onClick={() => run(row, "takeout")}
              className="block min-h-11 rounded-xl border border-outline-variant bg-white px-3 py-2 text-left text-[13px] font-semibold text-primary disabled:opacity-60"
            >
              <span className="flex items-center gap-2">
                {busy === "takeout" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                {t("actionTakeOut")}
              </span>
              <span className="block text-[11.5px] font-normal text-on-surface-variant">{t("actionTakeOutHint")}</span>
            </button>
            <button
              type="button"
              data-testid="review-xdoc-keep"
              disabled={busy !== null}
              onClick={() => run(row, "keep")}
              className="block min-h-11 rounded-xl border border-outline-variant bg-white px-3 py-2 text-left text-[13px] font-semibold text-primary disabled:opacity-60"
            >
              {t("actionKeep")}
              <span className="block text-[11.5px] font-normal text-on-surface-variant">{t("actionKeepHint")}</span>
            </button>
          </div>
        )}
        {stillThere === row.item.key && (
          <p data-testid="review-xdoc-still-there" className="text-[13px] text-on-surface-variant">
            {t("takenOutStillThere")}
          </p>
        )}
      </div>
    );
  };

  return (
    <div data-testid="review-xdoc" className="flex flex-col gap-2 border-t border-outline-variant pt-2">
      <span className="font-heading text-[13px] font-bold text-on-surface">{t("sectionTitle")}</span>
      <span data-testid="review-xdoc-sub" className="text-xs text-on-surface-variant">
        {t("sectionSub", { decided: section.decided, total: section.rows.length })}
      </span>
      {current && card(current)}
      <ul className="flex flex-col gap-2">
        {section.rows
          .filter((r) => r.item.key !== current?.item.key)
          .map((r) => (
            <li key={r.item.key}>
              <button
                type="button"
                data-testid="review-xdoc-row"
                data-status={r.status}
                onClick={() => setOpenKey(r.item.key)}
                className="flex min-h-11 w-full items-center gap-2.5 rounded-xl border border-outline-variant bg-surface-bright px-3 text-left"
              >
                <span
                  aria-hidden="true"
                  className={`h-2 w-2 shrink-0 rounded-full ${r.status === "open" ? "bg-warning" : "bg-success"}`}
                />
                <span className="flex min-w-0 flex-1 flex-col py-1.5">
                  <span className="text-[13px] text-on-surface">{r.item.concepts.join(", ")}</span>
                  <span className="text-xs text-on-surface-variant">
                    {r.item.kinds.includes("letter_only") ? t("rowOnly") : t("rowRicher")}
                  </span>
                </span>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ${
                    r.status === "open" ? "bg-surface-container-high text-primary" : "bg-success-container text-success"
                  }`}
                >
                  {r.status === "open" ? t("rowDecide") : STATUS_LABEL[r.status]}
                </span>
                <ChevronRight className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
              </button>
            </li>
          ))}
      </ul>
      {error && (
        <p data-testid="review-xdoc-error" role="alert" className="text-[13px] text-critical">
          {error}
        </p>
      )}
    </div>
  );
}
