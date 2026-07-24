// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// frontend/components/cv/TruthfulnessPanel.tsx
"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import type { ATSReport } from "@/components/cv/ATSChecksPanel";

// E043/US247 (ADR-052 §4): per-claim truthfulness report panel — sibling of
// ATSChecksPanel in the document workspace. Red flags (inflated /
// misattributed / unbacked) stay loud on the compact card; unverifiable soft
// claims are a single muted note, never a wall of warnings.

type Verdict = "grounded" | "inflated" | "misattributed" | "unbacked" | "unverifiable";

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

const FLAG_VERDICTS: Verdict[] = ["inflated", "misattributed", "unbacked"];

// #237 (F14): a report with no red flags can still be dominated by claims the
// vault simply couldn't check (the letter path's whole-sentence claims almost
// never clear the grounding floor) — that must never render as the same
// green "everything backed" headline as a genuinely well-grounded document.
// Floor avoids noise on tiny documents (e.g. a 2-claim all-green report).
const UNVERIFIABLE_DOMINANCE_FLOOR = 3;

function isUnverifiableDominant(
  groundedCount: number,
  unverifiableCount: number,
  total: number,
): boolean {
  return total >= UNVERIFIABLE_DOMINANCE_FLOOR && unverifiableCount > groundedCount;
}

// E048/US266 (#249 option b): "related" is a FRONTEND-ONLY display state, never
// a backend verdict (the Oracle verdict taxonomy in schemas/oracle.py is
// untouched, per ADR-052 §3 / #249's hard boundary). It reclassifies how an
// "unbacked" skill claim RENDERS when the Keyword Ledger's own adjacency
// classification already vouches for the same concept — never a change to
// what the Oracle itself concluded.
type DisplayKind = Verdict | "related";

const VERDICT_CHIP_CLASS: Record<DisplayKind, string> = {
  grounded: "bg-success-container text-success",
  inflated: "bg-critical-container text-critical",
  misattributed: "bg-critical-container text-critical",
  unbacked: "bg-warning-container text-warning",
  unverifiable: "border border-outline-variant text-on-surface-variant",
  // Deliberately neither the red (flag) nor the green (grounded) palette —
  // a genuinely neutral/informational chip (#249: must not look like either).
  related: "bg-primary-container text-primary",
};

function VerdictChip({ kind, label }: { kind: DisplayKind; label: string }) {
  return (
    <span
      data-testid={`truthfulness-chip-${kind}`}
      className={`inline-block shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${VERDICT_CHIP_CLASS[kind]}`}
    >
      {label}
    </span>
  );
}

// Simple, deterministic client-side fold (#249: "keep it simple and
// deterministic") — case and surrounding-whitespace only, no fuzzy matching.
function foldSkillText(s: string): string {
  return s.trim().toLowerCase();
}

/** Does this claim's text match a claimable Keyword Ledger concept? */
function isLedgerClaimable(text: string, claimableSet: Set<string>): boolean {
  return claimableSet.has(foldSkillText(text));
}

export default function TruthfulnessPanel({
  report,
  atsReport,
}: {
  report: TruthfulnessReport;
  // E048/US266 (#249 option b): optional — the sibling ATS report, whose
  // Keyword Ledger `claimable_concepts` drive the third-state join. Absent
  // entirely, behaviour is unchanged (back-compat: no reclassification).
  atsReport?: ATSReport;
}) {
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

  // E048/US266 (#249 option b): a skill claim the Oracle calls "unbacked"
  // whose text matches a Keyword Ledger CLAIMABLE concept (present OR missing
  // in the document — presence is irrelevant to this join) has RELATED vault
  // evidence, just not a literal hit. Rendering it as a plain red flag next
  // to the ATS panel calling the same concept "claimable" is the exact
  // contradiction #249 reported; rendering it plain green would overclaim.
  // Third state: visible, neutral, excluded from the red-flag headline.
  const claimableSet = new Set(
    (atsReport?.keywords.claimable_concepts ?? []).map(foldSkillText),
  );
  const isRelated = (c: TruthfulnessClaimResult) =>
    c.verdict.verdict === "unbacked" &&
    c.claim.kind === "skill" &&
    isLedgerClaimable(c.claim.text, claimableSet);

  const flagged = claims.filter(
    (c) => FLAG_VERDICTS.includes(c.verdict.verdict) && !isRelated(c),
  );
  const related = claims.filter((c) => isRelated(c));
  const unverifiable = claims.filter((c) => c.verdict.verdict === "unverifiable");
  const groundedCount = claims.filter((c) => c.verdict.verdict === "grounded").length;
  const unverifiableDominant =
    flagged.length === 0 &&
    isUnverifiableDominant(groundedCount, unverifiable.length, claims.length);
  const verdictLabel = (v: Verdict) => t(`verdicts.${v}`);

  // Drawer ordering: red flags first, then related-evidence, then
  // unverifiable, grounded last.
  const ordered = [
    ...flagged,
    ...related,
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
                flagged.length > 0
                  ? "text-critical"
                  : unverifiableDominant
                    ? "text-warning"
                    : "text-on-surface"
              }`}
            >
              <span
                aria-hidden="true"
                className={`text-xs font-bold ${
                  flagged.length > 0
                    ? "text-critical"
                    : unverifiableDominant
                      ? "text-warning"
                      : "text-success"
                }`}
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative pass/fail/warn glyphs */}
                {flagged.length > 0 ? "✗" : unverifiableDominant ? "!" : "✓"}
              </span>
              {flagged.length > 0
                ? t("needsReview", { count: flagged.length })
                : unverifiableDominant
                  ? t("mostlyUnverifiable", {
                      unverifiable: unverifiable.length,
                      total: claims.length,
                    })
                  : t("allClear", { count: claims.length })}
            </p>
            {unverifiable.length > 0 && !unverifiableDominant && (
              <p
                data-testid="truthfulness-unverifiable-note"
                className="text-xs text-on-surface-variant"
              >
                {t("unverifiableNote", { count: unverifiable.length })}
              </p>
            )}
            {related.length > 0 && (
              <p
                data-testid="truthfulness-related-note"
                className="text-xs text-on-surface-variant"
              >
                {t("relatedNote", { count: related.length })}
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
                  kind={c.verdict.verdict}
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

        {/* E048/US266 (#249): visible but NEITHER red nor green — a neutral,
            informational third state, distinct from both the loud red-flag
            list above and the plain grounded/green claims. */}
        {related.length > 0 && (
          <ul className="mt-2 space-y-1.5 border-t border-outline-variant pt-2">
            {related.map((c) => (
              <li
                key={c.claim.location}
                data-testid={`truthfulness-related-${c.claim.location}`}
                className="flex items-start gap-2 text-sm text-on-surface"
              >
                <VerdictChip kind="related" label={t("verdicts.related")} />
                <span className="min-w-0">
                  <span className="block">{c.claim.text}</span>
                  <span className="block text-xs text-on-surface-variant">
                    {t("relatedDetail")}
                  </span>
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
                const claimIsRelated = isRelated(c);
                // #249: a related-evidence claim gets its own honest chip/detail —
                // never the oracle's raw "no vault evidence" wording alongside the
                // ledger's "claimable" framing (that pairing IS the contradiction
                // this feature resolves).
                const chipKind: DisplayKind = claimIsRelated ? "related" : c.verdict.verdict;
                const chipLabel = claimIsRelated ? t("verdicts.related") : verdictLabel(c.verdict.verdict);
                const detailText = claimIsRelated ? t("relatedDetail") : c.verdict.detail;
                return (
                  <li
                    key={c.claim.location}
                    data-testid={`truthfulness-drawer-claim-${c.claim.location}`}
                    className="space-y-1 text-sm text-on-surface"
                  >
                    <div className="flex items-start gap-2">
                      <VerdictChip kind={chipKind} label={chipLabel} />
                      <span className="min-w-0 flex-1">{c.claim.text}</span>
                    </div>
                    <p className="pl-1 font-mono text-[11px] text-on-surface-variant">
                      {c.claim.location}
                    </p>
                    {detailText ? (
                      <p className="pl-1 text-xs text-on-surface-variant">{detailText}</p>
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
