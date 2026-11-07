// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * Hazelnut build 7 WP-V (789:258 + 789:262, UX audit round 3 — Marcus, Priya):
 * an import WITHOUT a job posting ended on a bare upload page — no summary, no
 * next step, and the open profile question waited on /profile where the user
 * was not. On the in-flow import page this card says the profile is ready,
 * repeats the last import's one-line summary, counts the questions the health
 * hub holds (counted and linked, never answered here — ADR-099 cl. 10, one
 * condition, one place) and names the next step.
 *
 * Shown only when the flow has no job AND the last import is newer than the
 * flow — both stored facts; nothing is inferred.
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { fetchImportSummary, type ImportSummary } from "@/lib/api/importSummary";
import { isRenderedIssue } from "@/lib/profile-health";
import type { ProfileHealth } from "@/components/profile/HealthPanel";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

/** Hub threads that ask the user something (the rest are read-only or one-click fixes). */
export const QUESTION_THREADS = new Set(["conflict", "confirmation", "duplicate_entry", "aspiration"]);

export function openQuestionCount(health: ProfileHealth | null): number {
  return (health?.issues ?? []).filter((i) => QUESTION_THREADS.has(i.thread) && isRenderedIssue(i)).length;
}

/** The card's own condition: a flow without a job, and an import made after the flow began. */
export function importIsReadyForFlow(
  flow: { job_id: string | null; created_at: string } | null,
  summary: Pick<ImportSummary, "record_id" | "timestamp"> | null,
): boolean {
  if (!flow || flow.job_id || !summary?.record_id || !summary.timestamp) return false;
  return new Date(summary.timestamp).getTime() >= new Date(flow.created_at).getTime();
}

export function ImportReadyCard({ flowId, reloadKey }: { flowId: string; reloadKey?: unknown }) {
  const t = useTranslations("profileImport");
  const ts = useTranslations("importSummary");
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [ready, setReady] = useState(false);
  const [questions, setQuestions] = useState(0);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const [flowRes, s] = await Promise.all([
          fetch(`${API_BASE}/api/flow/${flowId}/state`),
          fetchImportSummary(),
        ]);
        const flow = flowRes.ok ? await flowRes.json() : null;
        if (!live) return;
        const ok = importIsReadyForFlow(flow, s);
        setSummary(s);
        setReady(ok);
        if (!ok) return;
        const healthRes = await fetch(`${API_BASE}/api/profile/health`);
        const health: ProfileHealth | null = healthRes.ok ? await healthRes.json() : null;
        if (live) setQuestions(openQuestionCount(health));
      } catch {
        if (live) setReady(false); // no card beats a wrong card
      }
    })();
    return () => {
      live = false;
    };
  }, [flowId, reloadKey]);

  if (!ready || !summary) return null;
  const matched = summary.matched.filter((m) => !m.undone_at).length;

  return (
    <div
      data-testid="import-ready-card"
      className="mb-5 rounded-xl border border-[#86efac] bg-[#f0fdf4] px-5 py-4"
    >
      <p className="flex items-center gap-2 font-manrope text-[15px] font-bold text-[#166534]">
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
        <span aria-hidden="true" className="material-symbols-outlined text-[20px]" style={{ fontVariationSettings: "'FILL' 1" }}>check_circle</span>
        {t("readyTitle")}
      </p>
      <p data-testid="import-ready-lead" className="mt-1 text-[13px] text-on-surface-variant">
        {ts("lastImportLead", { added: summary.added_total, matched })}
      </p>
      {questions > 0 && (
        <p data-testid="import-ready-questions" className="mt-3 flex flex-wrap items-center gap-2 text-[13px] font-semibold text-primary">
          {t("readyQuestions", { count: questions })}
          <Link
            href="/profile#profile-health"
            data-testid="import-ready-answer"
            className="rounded-full border border-primary/40 px-3 py-1 text-[12px] font-bold hover:bg-primary/5"
          >
            {t("readyAnswer")}
          </Link>
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px]">
        <span className="text-on-surface-variant">{t("readyNext")}</span>
        <Link
          href="/profile#import-summary"
          data-testid="import-ready-view-profile"
          className="rounded-full border border-outline-variant px-3 py-1 text-[12px] font-bold text-primary hover:bg-primary/5"
        >
          {t("readyViewProfile")}
        </Link>
        <Link
          href="/"
          data-testid="import-ready-check-job"
          className="rounded-full bg-primary px-3 py-1 text-[12px] font-bold text-white hover:bg-primary/90"
        >
          {t("readyCheckJob")}
        </Link>
      </div>
    </div>
  );
}
