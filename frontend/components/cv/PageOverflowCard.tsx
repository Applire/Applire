// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// frontend/components/cv/PageOverflowCard.tsx
//
// #786 L-3 (founder ruling 2026-10-22, option A): when the CV is longer than its
// page target, an amber card above the preview names what would have to go and
// lets the person keep the extra page or cut. Non-blocking; desktop and phone.
// "Kürzen" is deterministic (the backend swaps in the page ladder's proposal,
// exactly the items listed) and the choice is remembered per CV and can be
// changed. Self-contained: reads /status and posts /page-overflow itself, so the
// page mounts it with one line.
"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export type PageOverflowCutKind =
  | "duplicate_project"
  | "off_job_skills"
  | "repeated_industry"
  | "oldest_certification"
  | "oldest_education"
  // U-6: an old role (ended > 15 years ago) keeps title/company/dates only.
  | "old_role_bullets";

export interface PageOverflow {
  target: number;
  actual: number;
  /** Page count with nothing cut — what "keep" gives (A-3b). Older rows omit it. */
  full_pages?: number;
  proposed: { kind: PageOverflowCutKind; items: string[] }[];
  choice: "keep" | "cut" | null;
  cut_available: boolean;
  uncut_available: boolean;
  /**
   * INTERFACE B-2 (ADR-099 cl. 7): `false` = the ladder cannot reach the target
   * and the proposal is the closest reachable length; `null`/absent = older row.
   */
  reaches_target?: boolean | null;
  /** B-2: pages after the proposed cut; `null` = nothing can be cut → only "behalten". */
  cut_pages?: number | null;
  /**
   * D-23 (B): what the generation ALREADY left out to stay within target + 1
   * pages, per kind. `[]` (or absent) on older rows and when nothing was cut.
   */
  auto_cuts?: { kind: PageOverflowCutKind; items: string[] }[];
}

const WARNING_ICON = "warning";

const KIND_KEYS: Record<PageOverflowCutKind, string> = {
  duplicate_project: "pageOverflowKindDuplicateProject",
  off_job_skills: "pageOverflowKindOffJobSkills",
  repeated_industry: "pageOverflowKindRepeatedIndustry",
  oldest_certification: "pageOverflowKindOldestCertification",
  oldest_education: "pageOverflowKindOldestEducation",
  old_role_bullets: "pageOverflowKindOldRoleBullets",
};

interface PageOverflowCardProps {
  cvId: string;
  /** Called after a choice swapped the CV's content (cut, or back from a cut). */
  onContentChanged?: () => void;
  /**
   * ADR-099 cl. 3/7: the card is ONE counted class-A row of the review list
   * while undecided — the page counts it in the headline and the badges.
   */
  onOpenChange?: (open: boolean) => void;
}

export function PageOverflowCard({ cvId, onContentChanged, onOpenChange }: PageOverflowCardProps) {
  const t = useTranslations("cv");
  const tReview = useTranslations("documentReview.delivery");
  const [overflow, setOverflow] = useState<PageOverflow | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // The page mounts the card with key={cvId}, so a new CV starts from null.
    let cancelled = false;
    fetch(`${API_BASE}/api/cv/${cvId}/status`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { page_overflow?: PageOverflow | null } | null) => {
        if (!cancelled) setOverflow(data?.page_overflow ?? null);
      })
      .catch(() => {
        // Non-fatal — no card; the page-length finding still names the length.
      });
    return () => {
      cancelled = true;
    };
  }, [cvId]);

  // ADR-099 cl. 7: shown WHENEVER the CV is over target (B-2 sends the row
  // then) — no longer hidden when nothing is proposed.
  const open = Boolean(overflow && overflow.choice === null && overflow.actual > overflow.target);
  useEffect(() => {
    onOpenChange?.(open);
  }, [open, onOpenChange]);

  if (!overflow) return null;
  if (overflow.choice === null && overflow.actual <= overflow.target) return null;

  const { target, actual } = overflow;
  const tD = tReview;
  const cutPages = overflow.cut_pages === undefined ? target : overflow.cut_pages;
  const unreachable = overflow.reaches_target === false;
  // A-3b: after a cut `actual` is the cut CV's count; every "keep" label names
  // the count the person goes back to.
  const full = overflow.full_pages ?? actual;
  // U-6 (founder ruling 2026-10-28): an old role is SHORTENED (title, company
  // and dates stay), never removed — the copy says which of the two happens.
  const removed = overflow.proposed.filter((c) => c.kind !== "old_role_bullets").flatMap((c) => c.items).join(", ");
  const shortened = overflow.proposed.filter((c) => c.kind === "old_role_bullets").flatMap((c) => c.items).join(", ");
  const mode = shortened ? "shorten" : "remove";
  // D-23: only kinds the card can name (a kind it does not know renders nothing).
  const autoCuts = (overflow.auto_cuts ?? []).filter((c) => c.kind in KIND_KEYS && c.items.length > 0);

  async function choose(choice: "keep" | "cut") {
    if (busy || !overflow) return;
    const swapsContent = choice === "cut" || overflow.choice === "cut";
    setBusy(true);
    setFailed(false);
    try {
      const res = await fetch(`${API_BASE}/api/cv/${cvId}/page-overflow`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ choice }),
      });
      if (res.status === 409 && overflow.choice === "cut") {
        // MD3-3: the switch is no longer possible on the CV as it stands (an edit
        // since the cut left an item nowhere to go). Re-read the state so the
        // card drops the switch and says why, instead of a generic error.
        const fresh = await fetch(`${API_BASE}/api/cv/${cvId}/status`);
        const data = fresh.ok ? ((await fresh.json()) as { page_overflow?: PageOverflow | null }) : null;
        if (data?.page_overflow) {
          setOverflow(data.page_overflow);
          return;
        }
      }
      if (!res.ok) throw new Error(`page-overflow ${res.status}`);
      setOverflow((await res.json()) as PageOverflow);
      if (swapsContent) onContentChanged?.();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  const error = failed ? (
    <p className="mt-2 text-xs text-critical" role="alert" data-testid="cv-page-overflow-error">
      {t("pageOverflowError")}
    </p>
  ) : null;

  if (overflow.choice !== null) {
    const cut = overflow.choice === "cut";
    const canSwitch = cut ? overflow.uncut_available : overflow.cut_available;
    return (
      <div
        className="shrink-0 rounded-lg border border-outline-variant bg-surface-container px-3 py-2 text-[13px] text-on-surface"
        data-testid="cv-page-overflow-done"
        data-choice={overflow.choice}
      >
        <span>
          {cut
            ? removed && shortened
              ? t("pageOverflowCutDoneBoth", { target, items: removed, roles: shortened })
              : shortened
                ? t("pageOverflowCutDoneShortened", { target, roles: shortened })
                : t("pageOverflowCutDone", { target, items: removed })
            : t("pageOverflowKeptDone", { actual: full })}
        </span>
        {cut && !canSwitch && (
          <span className="ml-1 text-on-surface-variant" data-testid="cv-page-overflow-undo-gone">
            {t("pageOverflowUndoGone")}
          </span>
        )}
        {canSwitch && (
          <button
            type="button"
            onClick={() => void choose(cut ? "keep" : "cut")}
            disabled={busy}
            className="ml-1 font-semibold text-primary underline underline-offset-2 disabled:opacity-50"
            data-testid="cv-page-overflow-switch"
          >
            {cut ? t("pageOverflowUndoCut", { actual: full }) : t("pageOverflowUndoKeep", { target })}
          </button>
        )}
        {error}
      </div>
    );
  }

  return (
    <section
      className="shrink-0 rounded-xl border border-outline-variant bg-surface-bright px-3 py-3 text-on-surface"
      data-testid="cv-page-overflow-card"
      aria-labelledby="cv-page-overflow-title"
    >
      <h2 id="cv-page-overflow-title" className="mb-1 flex items-center gap-2 text-[15px] font-semibold">
        <span className="material-symbols-outlined text-warning" aria-hidden="true" style={{ fontSize: 18 }}>
          {WARNING_ICON}
        </span>
        <span className="hidden sm:inline">{t("pageOverflowTitle", { actual, target })}</span>
        <span className="sm:hidden">{t("pageOverflowTitleShort", { actual, target })}</span>
      </h2>
      {autoCuts.length > 0 && (
        <div className="mb-2 text-[13px] leading-snug text-on-surface-variant" data-testid="cv-page-overflow-auto-cuts">
          <p className="mb-1">{t("pageOverflowAutoCutsLead", { limit: target + 1 })}</p>
          <ul className="ml-5 list-disc">
            {autoCuts.map((c) => (
              <li key={c.kind} data-kind={c.kind}>
                {t("pageOverflowCutLine", { label: t(KIND_KEYS[c.kind]), items: c.items.join(", ") })}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="mb-1 text-[13px] leading-snug text-on-surface-variant" data-testid="cv-page-overflow-body">
        {cutPages === null
          ? tD("cvPageNothingToCut")
          : unreachable
            ? tD("cvPageUnreachable", { target, reachable: cutPages, mode })
            : t("pageOverflowBody", { target, mode })}
      </p>
      <ul className="mb-3 ml-5 list-disc text-[13px] leading-snug text-on-surface-variant" data-testid="cv-page-overflow-cuts">
        {overflow.proposed.map((c) => (
          <li key={c.kind} data-kind={c.kind}>
            {t("pageOverflowCutLine", { label: t(KIND_KEYS[c.kind]), items: c.items.join(", ") })}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void choose("keep")}
          disabled={busy}
          className="rounded-lg bg-primary px-4 py-2 text-[13px] font-bold text-white hover:opacity-90 disabled:opacity-50"
          data-testid="cv-page-overflow-keep"
        >
          {t("pageOverflowKeep", { actual: full })}
        </button>
        {cutPages !== null && (
          <button
            type="button"
            onClick={() => void choose("cut")}
            disabled={busy || !overflow.cut_available}
            className="rounded-lg border border-outline-variant bg-white px-4 py-2 text-[13px] font-semibold text-on-surface disabled:opacity-50"
            data-testid="cv-page-overflow-cut"
          >
            {t("pageOverflowCut", { target: cutPages })}
          </button>
        )}
      </div>
      {cutPages !== null && !overflow.cut_available && (
        <p className="mt-2 text-xs text-on-surface-variant" data-testid="cv-page-overflow-cut-gone">
          {t("pageOverflowCutGone")}
        </p>
      )}
      <p className="mt-2 text-xs text-on-surface-variant">{t("pageOverflowNote")}</p>
      {error}
    </section>
  );
}
