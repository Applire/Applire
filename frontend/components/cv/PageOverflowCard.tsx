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
  | "oldest_education";

export interface PageOverflow {
  target: number;
  actual: number;
  /** Page count with nothing cut — what "keep" gives (A-3b). Older rows omit it. */
  full_pages?: number;
  proposed: { kind: PageOverflowCutKind; items: string[] }[];
  choice: "keep" | "cut" | null;
  cut_available: boolean;
  uncut_available: boolean;
}

const WARNING_ICON = "warning";

const KIND_KEYS: Record<PageOverflowCutKind, string> = {
  duplicate_project: "pageOverflowKindDuplicateProject",
  off_job_skills: "pageOverflowKindOffJobSkills",
  repeated_industry: "pageOverflowKindRepeatedIndustry",
  oldest_certification: "pageOverflowKindOldestCertification",
  oldest_education: "pageOverflowKindOldestEducation",
};

interface PageOverflowCardProps {
  cvId: string;
  /** Called after a choice swapped the CV's content (cut, or back from a cut). */
  onContentChanged?: () => void;
}

export function PageOverflowCard({ cvId, onContentChanged }: PageOverflowCardProps) {
  const t = useTranslations("cv");
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

  if (!overflow) return null;
  if (overflow.choice === null && overflow.proposed.length === 0) return null;

  const { target, actual } = overflow;
  // A-3b: after a cut `actual` is the cut CV's count; every "keep" label names
  // the count the person goes back to.
  const full = overflow.full_pages ?? actual;
  const removed = overflow.proposed.flatMap((c) => c.items).join(", ");

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
        className="mb-3 shrink-0 rounded-lg border border-outline-variant bg-surface-container px-3 py-2 text-[13px] text-on-surface"
        data-testid="cv-page-overflow-done"
        data-choice={overflow.choice}
      >
        <span>
          {cut
            ? t("pageOverflowCutDone", { target, items: removed })
            : t("pageOverflowKeptDone", { actual: full })}
        </span>
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
      className="mb-3 max-h-[45%] shrink-0 overflow-y-auto rounded-lg border border-warning/40 bg-warning-container px-4 py-3 text-on-surface"
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
      <p className="mb-1 text-[13px] leading-snug text-on-surface-variant">
        {t("pageOverflowBody", { target })}
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
        <button
          type="button"
          onClick={() => void choose("cut")}
          disabled={busy || !overflow.cut_available}
          className="rounded-lg border border-outline-variant bg-white px-4 py-2 text-[13px] font-semibold text-on-surface disabled:opacity-50"
          data-testid="cv-page-overflow-cut"
        >
          {t("pageOverflowCut", { target })}
        </button>
      </div>
      <p className="mt-2 text-xs text-on-surface-variant">{t("pageOverflowNote")}</p>
      {error}
    </section>
  );
}
