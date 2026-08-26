// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

// frontend/components/cv/ATSChecksPanel.tsx
"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Info } from "lucide-react";
import { Button } from "@/components/ui/button";

type ATSCheck = {
  id: string;
  status: "pass" | "fail";
  details?: string | null;
  // E042 follow-up (ADR-038): machine-readable twin of `details` for bands the
  // frontend localises; `details` stays the EN fallback for legacy reports.
  details_key?: string | null;
  details_params?: Record<string, string | number> | null;
  // E056/ADR-077 clause 5: structured driver for a fail band — currently only
  // {"pinned_facts": N} on the page-length check (N present pinned facts).
  driver?: Record<string, number> | null;
};
// E056/ADR-077 clauses 3+5: one fact pin's measured fate on THIS document —
// present in the tailored twin, stale (excluded from generation), or removed
// by a truth floor (hierarchy: truth > pin, never silent). Ship-and-report,
// never a gate.
export type PinnedFactReportEntry = {
  pin_id: string;
  entry_type: string;
  quote: string;
  present: boolean;
  stale: boolean;
  removed_by_truth_floor?: boolean;
  // #580: the job's do-not-claim terms this pinned quote carries — a fact
  // about the quote, never a statement about why the pin is absent. Optional
  // for back-compat with reports persisted before this field existed.
  ledger_conflict?: string[];
};
export type ATSReport = {
  checks: ATSCheck[];
  // null/absent = audited without pin context (legacy reports, no pins).
  pinned_facts?: PinnedFactReportEntry[] | null;
  keywords: {
    present: string[];
    missing: string[];
    // US203 (ADR-048): a missing keyword the candidate HAS per the Keyword Ledger
    // (a surfacing miss — fixable) vs one they genuinely lack (an honest gap, never
    // something to fabricate). Optional for back-compat with legacy reports.
    missing_claimable?: string[];
    missing_honest_gap?: string[];
    // #117 (ADR-048 fourth quadrant): present in the document WITHOUT profile backing —
    // an unsupported claim (truthfulness warning). Optional for back-compat.
    present_unsupported?: string[];
    // E048/US266 (#249 option b): EVERY claimable Keyword Ledger entry's surface
    // forms (concept name included), regardless of presence in the document —
    // lets TruthfulnessPanel join an Oracle "unbacked" skill claim against a
    // ledger concept the candidate supports only via semantic adjacency, so
    // that case renders as a distinct honest "related evidence" state instead
    // of a contradiction between the two panels. Optional for back-compat.
    claimable_concepts?: string[];
  };
} | null;

// Strip trailing numeric index (e.g. "work-1" → "work", "education-2" → "education", "body-3" → "body")
const baseId = (id: string) => id.replace(/-\d+$/, "");

// E042 follow-up (ADR-038): detail keys with a translation under ats.checkDetails.
// Only whitelisted keys go through t() — an unknown key from a newer backend falls
// back to the EN `details` string instead of rendering a raw key path.
const LOCALIZED_DETAIL_KEYS = new Set([
  "page-length-target",
  // #238 (founder-acceptance F4): an explicit page target the condense loop
  // could not hit — a genuine miss, never dressed up as senior-profile
  // advice. Ships with status="fail" (see ats_audit.py), so it renders
  // through the existing failed-check path — red, inline, no new UI state.
  "page-length-target-missed",
  "page-length-senior",
  "page-length-exhausted",
  "page-length-exceeds",
  "page-length-letter",
  // #391 interim (ADR-076 amendment 4 point 6): measurement-only advisory —
  // ships as a passing check with a localized `details` sentence, same shape
  // as the page-length advisory branches above.
  "skills-weak-vault-tie",
]);

type CheckGroup = { base: string; checks: ATSCheck[] };

function groupChecks(checks: ATSCheck[]): CheckGroup[] {
  const byBase = new Map<string, CheckGroup>();
  const groups: CheckGroup[] = [];
  for (const c of checks) {
    const b = baseId(c.id);
    let g = byBase.get(b);
    if (!g) {
      g = { base: b, checks: [] };
      byBase.set(b, g);
      groups.push(g);
    }
    g.checks.push(c);
  }
  return groups;
}

// Keyword-coverage ring — visualises the present/total COUNT (a fact from the
// report), never an aggregate quality score (ADR-035/ADR-039: no synthetic scores).
function KeywordRing({ present, total, label }: { present: number; total: number; label: string }) {
  const radius = 20;
  const circumference = 2 * Math.PI * radius;
  const fraction = total > 0 ? present / total : 1;
  return (
    <svg width="52" height="52" viewBox="0 0 52 52" role="img" aria-label={label}>
      <circle cx="26" cy="26" r={radius} fill="none" stroke="#e5e7eb" strokeWidth="5" />
      <circle
        cx="26"
        cy="26"
        r={radius}
        fill="none"
        stroke="#C9A84C"
        strokeWidth="5"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - fraction)}
        transform="rotate(-90 26 26)"
      />
      <text
        x="26"
        y="30"
        textAnchor="middle"
        fontSize="12"
        fontWeight="700"
        fill="#1B4F72"
        fontFamily="Poppins, sans-serif"
      >
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- numeric fraction separator */}
        {present}/{total}
      </text>
    </svg>
  );
}

export default function ATSChecksPanel({ report }: { report: ATSReport }) {
  const t = useTranslations("ats");
  const tCommon = useTranslations("common");
  const [open, setOpen] = useState(false);

  // Close the drawer on Escape
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
        data-testid="ats-unavailable"
        className="rounded-xl border border-outline-variant surface-glass p-4 text-sm text-on-surface-variant"
      >
        {t("unavailable")}
      </div>
    );
  }

  const labelKey = (id: string) => `checks.${baseId(id)}`;
  // Localised detail when the backend sent a known key WITH its params; EN `details`
  // otherwise. The params check matters: next-intl doesn't throw on a missing ICU
  // variable — it renders the raw key path — so a keyed check without params
  // (partially-migrated persisted report) must take the fallback, not t().
  const detailText = (c: ATSCheck) =>
    c.details_key && c.details_params && LOCALIZED_DETAIL_KEYS.has(c.details_key)
      ? t(`checkDetails.${c.details_key}`, c.details_params)
      : c.details;
  const failed = report.checks.filter((c) => c.status === "fail");
  // E042/US239 (ADR-051): a PASSING check can carry a non-null `details` string
  // (e.g. page-length passing "beyond the norm by choice"/"acceptable for senior
  // profiles"). These are informational, never failures — style and group them
  // separately so a pass-with-advisory never reads as a problem.
  const passingAdvisory = report.checks.filter((c) => c.status === "pass" && c.details);
  const present = report.keywords.present.length;
  const total = present + report.keywords.missing.length;
  const coverageLabel = t("keywordCoverage", { present, total });

  // US203: split missing keywords into claimable (held but absent — fixable by surfacing)
  // vs honest gap (not in the profile). Legacy reports without the buckets fall back to
  // showing the flat missing list as honest gaps so nothing regresses.
  // A report is "bucketed" once the backend annotated it (US203). Legacy reports lack the
  // fields entirely and fall back to a single flat missing line.
  const isBucketed = report.keywords.missing_claimable !== undefined;
  const missingClaimable = report.keywords.missing_claimable ?? [];
  const missingHonestGap = report.keywords.missing_honest_gap ?? [];
  const presentUnsupported = report.keywords.present_unsupported ?? [];

  // #234 (Tiramisu founder-acceptance F1): a document with zero failing ATSChecks
  // can still be missing keywords the vault genuinely supports — the #234 bullet-
  // retention guard runs best-effort and does not guarantee every claimable term
  // survives. That case must never render under the plain green all-clear
  // headline; it gets its own distinct (non-green, non-failure) state.
  const structurePassed = failed.length === 0;
  const hasUnsurfacedClaimable = structurePassed && missingClaimable.length > 0;

  // E056/ADR-077: null/absent = audited without pin context (legacy reports).
  const pinnedFacts = report.pinned_facts ?? [];

  return (
    <>
      <section
        data-testid="ats-panel"
        aria-label={t("title")}
        className="rounded-xl border border-outline-variant surface-glass px-4 py-2.5"
      >
        <div className="flex items-center gap-4">
          <div data-testid="ats-keywords-coverage" className="shrink-0" title={coverageLabel}>
            <KeywordRing present={present} total={total} label={coverageLabel} />
          </div>

          <div className="min-w-0 flex-1 space-y-0.5">
            <p
              data-testid="ats-structure-status"
              className={`flex items-center gap-1.5 text-sm font-medium ${
                !structurePassed ? "text-critical" : "text-on-surface"
              }`}
            >
              <span
                aria-hidden="true"
                className={`text-xs font-bold ${
                  !structurePassed ? "text-critical" : hasUnsurfacedClaimable ? "text-warning" : "text-success"
                }`}
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative pass/fail/advisory glyphs */}
                {!structurePassed ? "✗" : hasUnsurfacedClaimable ? "!" : "✓"}
              </span>
              {!structurePassed
                ? t("structureIssues", { count: failed.length })
                : hasUnsurfacedClaimable
                  ? t("structureOkKeywordsMissing", { count: missingClaimable.length })
                  : t("structureOk")}
            </p>
            {missingClaimable.length > 0 && (
              <p
                data-testid="ats-keywords-missing-claimable"
                className="text-xs text-on-surface-variant"
              >
                {t("missingClaimable", { count: missingClaimable.length })}
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                {" "}
                <span className="text-on-surface">{missingClaimable.join(", ")}</span>
              </p>
            )}
            {missingHonestGap.length > 0 && (
              <p
                data-testid="ats-keywords-missing-honest-gap"
                className="text-xs text-on-surface-variant"
              >
                {t("missingHonestGap", { count: missingHonestGap.length })}
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                {" "}
                <span className="text-on-surface">{missingHonestGap.join(", ")}</span>
              </p>
            )}
            {presentUnsupported.length > 0 && (
              <p
                data-testid="ats-keywords-present-unsupported"
                className="text-xs font-medium text-critical"
              >
                {t("presentUnsupported", { count: presentUnsupported.length })}
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                {" "}
                <span>{presentUnsupported.join(", ")}</span>
              </p>
            )}
            {/* Back-compat sentinel: a legacy report (no buckets) still renders a flat line */}
            {!isBucketed && report.keywords.missing.length > 0 && (
                <p
                  data-testid="ats-keywords-missing"
                  className="truncate text-xs text-on-surface-variant"
                >
                  {t("missingKeywords", { count: report.keywords.missing.length })}
                  {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                  {" "}
                  <span className="text-on-surface">{report.keywords.missing.join(", ")}</span>
                </p>
              )}
          </div>

          <Button
            data-testid="ats-details-button"
            size="sm"
            variant="outline"
            className="shrink-0"
            onClick={() => setOpen(true)}
          >
            {t("detailsButton")}
          </Button>
        </div>

        {/* Failures stay loud: rendered inline on the compact card, no interaction needed.
            Pass-with-advisory checks (e.g. page-length beyond the norm by choice) render
            alongside them with informational — not failure — styling. */}
        {(failed.length > 0 || passingAdvisory.length > 0) && (
          <ul className="mt-2 space-y-1 border-t border-outline-variant pt-2">
            {failed.map((c) => (
              <li
                key={c.id}
                data-testid={`ats-check-${c.id}`}
                className="flex items-start gap-2 text-sm text-on-surface"
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative fail glyph */}
                <span aria-hidden="true" className="mt-0.5 shrink-0 text-xs font-bold text-critical">✗</span>
                <span>
                  {t(labelKey(c.id))}
                  {c.details ? (
                    <span className="text-on-surface-variant">
                      {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative em-dash separator */}
                      {" — "}
                      {detailText(c)}
                    </span>
                  ) : null}
                  {/* E056/ADR-077 clause 5: the page-length band's structured
                      driver — pins may honestly push a document over the
                      norm; that overrun is reported, never repaired. */}
                  {c.driver?.pinned_facts ? (
                    <span
                      data-testid={`ats-check-${c.id}-pin-driver`}
                      className="block text-xs text-on-surface-variant"
                    >
                      {t("pinnedFacts.driverLine", { count: c.driver.pinned_facts })}
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
            {passingAdvisory.map((c) => (
              <li
                key={c.id}
                data-testid={`ats-advisory-${c.id}`}
                className="flex items-start gap-2 text-sm text-on-surface"
              >
                <Info aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                <span>
                  {t(labelKey(c.id))}
                  <span className="text-on-surface-variant">
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative em-dash separator */}
                    {" — "}
                    {detailText(c)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}

        {/* E056/ADR-077 clauses 3+5: per-pin presence measurement on THIS
            document — ship-and-report, never a gate. Rendered whenever the
            report carries pin context, independent of pass/fail status. */}
        {pinnedFacts.length > 0 && (
          <div
            data-testid="ats-pinned-facts"
            className="mt-2 space-y-1 border-t border-outline-variant pt-2"
          >
            <p className="text-xs font-semibold uppercase tracking-wide text-on-surface-variant">
              {t("pinnedFacts.title")}
            </p>
            <ul className="space-y-1">
              {pinnedFacts.map((pin) => (
                <li
                  key={pin.pin_id}
                  data-testid={`ats-pinned-fact-${pin.pin_id}`}
                  className="flex items-start gap-2 text-sm text-on-surface"
                >
                  <span
                    aria-hidden="true"
                    className={`mt-0.5 shrink-0 text-xs font-bold ${
                      pin.present ? "text-success" : "text-critical"
                    }`}
                  >
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative pass/fail glyphs */}
                    {pin.present ? "✓" : "✗"}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate" title={pin.quote}>
                      {pin.quote}
                    </span>
                    <span className="flex flex-wrap items-center gap-1.5 text-xs text-on-surface-variant">
                      {!pin.present && (
                        <span data-testid={`ats-pinned-fact-unmet-${pin.pin_id}`}>
                          {t("pinnedFacts.unmet")}
                          {pin.ledger_conflict && pin.ledger_conflict.length > 0 && (
                            <>
                              {" "}
                              {t("pinnedFacts.ledgerConflict", {
                                terms: pin.ledger_conflict.join(", "),
                              })}
                            </>
                          )}
                        </span>
                      )}
                      {pin.stale && (
                        <span
                          data-testid={`ats-pinned-fact-stale-${pin.pin_id}`}
                          className="rounded-full bg-warning-container px-2 py-0.5 font-medium text-on-surface"
                        >
                          {t("pinnedFacts.stale")}
                        </span>
                      )}
                      {pin.removed_by_truth_floor && (
                        <span data-testid={`ats-pinned-fact-floor-${pin.pin_id}`}>
                          {t("pinnedFacts.removedByTruthFloor")}
                        </span>
                      )}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {open && (
        <>
          <button
            type="button"
            data-testid="ats-drawer-backdrop"
            aria-label={tCommon("close")}
            className="fixed inset-0 z-40 bg-black/30"
            onClick={() => setOpen(false)}
          />
          <aside
            role="dialog"
            aria-modal="true"
            aria-label={t("title")}
            data-testid="ats-drawer"
            className="fixed inset-y-0 right-0 z-50 w-full max-w-[380px] overflow-y-auto border-l border-outline-variant bg-white p-5 shadow-xl"
          >
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-sm font-heading font-semibold text-on-surface">{t("title")}</h3>
              <button
                type="button"
                data-testid="ats-drawer-close"
                aria-label={tCommon("close")}
                className="text-lg leading-none text-on-surface-variant hover:text-on-surface"
                onClick={() => setOpen(false)}
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative close glyph */}
                <span aria-hidden="true">×</span>
              </button>
            </div>

            <ul className="space-y-1.5">
              {groupChecks(report.checks).map((g) => {
                const passed = g.checks.filter((c) => c.status === "pass").length;
                const groupOk = passed === g.checks.length;
                const failing = g.checks.filter((c) => c.status === "fail");
                const advisory = g.checks.filter((c) => c.status === "pass" && c.details);
                return (
                  <li
                    key={g.base}
                    data-testid={`ats-drawer-check-${g.base}`}
                    className="flex items-start gap-2 text-sm text-on-surface"
                  >
                    <span
                      aria-hidden="true"
                      className={`mt-0.5 shrink-0 text-xs font-bold ${groupOk ? "text-success" : "text-critical"}`}
                    >
                      {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative pass/fail glyphs */}
                      {groupOk ? "✓" : "✗"}
                    </span>
                    <span>
                      {t(`checks.${g.base}`)}
                      {g.checks.length > 1 && (
                        <span className="text-on-surface-variant">
                          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space before count */}
                          {" "}
                          {t("groupCount", { passed, total: g.checks.length })}
                        </span>
                      )}
                      {failing.map((c) =>
                        c.details ? (
                          <span key={c.id} className="block text-xs text-on-surface-variant">
                            {detailText(c)}
                          </span>
                        ) : null,
                      )}
                      {/* Pass-with-advisory detail — informational styling (text-primary),
                          never the failure gray/red used above. */}
                      {advisory.map((c) => (
                        <span
                          key={c.id}
                          data-testid={`ats-drawer-advisory-${c.id}`}
                          className="block text-xs text-primary"
                        >
                          {detailText(c)}
                        </span>
                      ))}
                    </span>
                  </li>
                );
              })}
            </ul>

            <div className="mt-4 space-y-2 border-t border-outline-variant pt-3">
              <p data-testid="ats-drawer-coverage" className="text-sm text-on-surface-variant">
                {coverageLabel}
              </p>
              {isBucketed ? (
                <>
                  {missingClaimable.length > 0 && (
                    <p
                      data-testid="ats-drawer-missing-claimable"
                      className="text-sm text-on-surface-variant"
                    >
                      {t("missingClaimable", { count: missingClaimable.length })}
                      {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                      {" "}
                      <span className="text-on-surface">{missingClaimable.join(", ")}</span>
                    </p>
                  )}
                  {missingHonestGap.length > 0 && (
                    <p
                      data-testid="ats-drawer-missing-honest-gap"
                      className="text-sm text-on-surface-variant"
                    >
                      {t("missingHonestGap", { count: missingHonestGap.length })}
                      {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                      {" "}
                      <span className="text-on-surface">{missingHonestGap.join(", ")}</span>
                    </p>
                  )}
                </>
              ) : (
                report.keywords.missing.length > 0 && (
                  <p data-testid="ats-drawer-missing" className="text-sm text-on-surface-variant">
                    {t("missingKeywords", { count: report.keywords.missing.length })}
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                    {" "}
                    <span className="text-on-surface">{report.keywords.missing.join(", ")}</span>
                  </p>
                )
              )}
            </div>
          </aside>
        </>
      )}
    </>
  );
}
