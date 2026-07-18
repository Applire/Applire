// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// frontend/components/cv/TruthfulnessPanel.tsx
"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";

// E043/US247 (ADR-052 §4): per-claim truthfulness report panel — sibling of
// ATSChecksPanel in the document workspace. Red flags (inflated / unbacked)
// stay loud on the compact card; unverifiable soft claims are a single muted
// note, never a wall of warnings.

type Verdict = "grounded" | "inflated" | "unbacked" | "unverifiable";

type TruthfulnessEvidence = {
  kind: "profile_path" | "enrichment_record";
  ref: string;
  excerpt?: string;
};

export type TruthfulnessClaimResult = {
  claim: { text: string; location: string; kind: string };
  verdict: {
    verdict: Verdict;
    checker: string;
    evidence: TruthfulnessEvidence[];
    detail?: string | null;
  };
};

export type TruthfulnessReport = {
  version: string;
  document_kind: string;
  claims: TruthfulnessClaimResult[];
  counts: Record<string, number>;
  stated_limit: string;
} | null;

const FLAG_VERDICTS: Verdict[] = ["inflated", "unbacked"];

const VERDICT_CHIP_CLASS: Record<Verdict, string> = {
  grounded: "bg-success-container text-success",
  inflated: "bg-critical-container text-critical",
  unbacked: "bg-warning-container text-warning",
  unverifiable: "border border-outline-variant text-on-surface-variant",
};

function VerdictChip({ verdict, label }: { verdict: Verdict; label: string }) {
  return (
    <span
      data-testid={`truthfulness-chip-${verdict}`}
      className={`inline-block shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${VERDICT_CHIP_CLASS[verdict]}`}
    >
      {label}
    </span>
  );
}

export default function TruthfulnessPanel({ report }: { report: TruthfulnessReport }) {
  const t = useTranslations("truthfulness");
  const tCommon = useTranslations("common");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!report) {
    return (
      <div
        data-testid="truthfulness-unavailable"
        className="rounded-xl border border-outline-variant surface-glass p-4 text-sm text-on-surface-variant"
      >
        {t("unavailable")}
      </div>
    );
  }

  const claims = report.claims ?? [];
  const flagged = claims.filter((c) => FLAG_VERDICTS.includes(c.verdict.verdict));
  const unverifiable = claims.filter((c) => c.verdict.verdict === "unverifiable");
  const verdictLabel = (v: Verdict) => t(`verdicts.${v}`);

  // Drawer ordering: red flags first, then unverifiable, grounded last.
  const ordered = [
    ...flagged,
    ...unverifiable,
    ...claims.filter((c) => c.verdict.verdict === "grounded"),
  ];

  return (
    <>
      <section
        data-testid="truthfulness-panel"
        aria-label={t("title")}
        className="rounded-xl border border-outline-variant surface-glass px-4 py-2.5"
      >
        <div className="flex items-center gap-4">
          <div className="min-w-0 flex-1 space-y-0.5">
            <p
              data-testid="truthfulness-status"
              className={`flex items-center gap-1.5 text-sm font-medium ${
                flagged.length === 0 ? "text-on-surface" : "text-critical"
              }`}
            >
              <span
                aria-hidden="true"
                className={`text-xs font-bold ${flagged.length === 0 ? "text-success" : "text-critical"}`}
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative pass/fail glyphs */}
                {flagged.length === 0 ? "✓" : "✗"}
              </span>
              {flagged.length === 0
                ? t("allClear", { count: claims.length })
                : t("needsReview", { count: flagged.length })}
            </p>
            {unverifiable.length > 0 && (
              <p
                data-testid="truthfulness-unverifiable-note"
                className="text-xs text-on-surface-variant"
              >
                {t("unverifiableNote", { count: unverifiable.length })}
              </p>
            )}
          </div>

          <Button
            data-testid="truthfulness-details-button"
            size="sm"
            variant="outline"
            className="shrink-0"
            onClick={() => setOpen(true)}
          >
            {t("detailsButton")}
          </Button>
        </div>

        {/* Red flags stay loud: rendered inline, no interaction needed. */}
        {flagged.length > 0 && (
          <ul className="mt-2 space-y-1.5 border-t border-outline-variant pt-2">
            {flagged.map((c) => (
              <li
                key={c.claim.location}
                data-testid={`truthfulness-flag-${c.claim.location}`}
                className="flex items-start gap-2 text-sm text-on-surface"
              >
                <VerdictChip
                  verdict={c.verdict.verdict}
                  label={verdictLabel(c.verdict.verdict)}
                />
                <span className="min-w-0">
                  <span className="block">{c.claim.text}</span>
                  {c.verdict.detail ? (
                    <span className="block text-xs text-on-surface-variant">
                      {c.verdict.detail}
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {open && (
        <>
          <button
            type="button"
            data-testid="truthfulness-drawer-backdrop"
            aria-label={tCommon("close")}
            className="fixed inset-0 z-40 bg-black/30"
            onClick={() => setOpen(false)}
          />
          <aside
            role="dialog"
            aria-modal="true"
            aria-label={t("title")}
            data-testid="truthfulness-drawer"
            className="fixed inset-y-0 right-0 z-50 w-full max-w-[420px] overflow-y-auto border-l border-outline-variant bg-white p-5 shadow-xl"
          >
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-sm font-heading font-semibold text-on-surface">
                {t("title")}
              </h3>
              <button
                type="button"
                data-testid="truthfulness-drawer-close"
                aria-label={tCommon("close")}
                className="text-lg leading-none text-on-surface-variant hover:text-on-surface"
                onClick={() => setOpen(false)}
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative close glyph */}
                <span aria-hidden="true">×</span>
              </button>
            </div>

            <ul className="space-y-3">
              {ordered.map((c) => {
                const profileEvidence = c.verdict.evidence.filter(
                  (e) => e.kind === "profile_path",
                );
                return (
                  <li
                    key={c.claim.location}
                    data-testid={`truthfulness-drawer-claim-${c.claim.location}`}
                    className="space-y-1 text-sm text-on-surface"
                  >
                    <div className="flex items-start gap-2">
                      <VerdictChip
                        verdict={c.verdict.verdict}
                        label={verdictLabel(c.verdict.verdict)}
                      />
                      <span className="min-w-0 flex-1">{c.claim.text}</span>
                    </div>
                    <p className="pl-1 font-mono text-[11px] text-on-surface-variant">
                      {c.claim.location}
                    </p>
                    {c.verdict.detail ? (
                      <p className="pl-1 text-xs text-on-surface-variant">{c.verdict.detail}</p>
                    ) : null}
                    {profileEvidence.length > 0 && (
                      <div className="ml-1 border-l-2 border-outline-variant pl-2">
                        <p className="text-[11px] font-medium text-on-surface-variant">
                          {t("evidenceLabel")}
                        </p>
                        {profileEvidence.slice(0, 2).map((e) => (
                          <p key={e.ref} className="text-xs text-on-surface-variant">
                            {e.excerpt || e.ref}
                          </p>
                        ))}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>

            {/* ADR-052 §5 stated limit — always shown with the full report. */}
            <p
              data-testid="truthfulness-stated-limit"
              className="mt-4 border-t border-outline-variant pt-3 text-xs text-on-surface-variant"
            >
              {t("statedLimit")}
            </p>
          </aside>
        </>
      )}
    </>
  );
}
