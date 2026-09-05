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
import {
  baseId,
  usesLocalizedDetail,
  type ATSCheck,
  type ATSReport,
} from "@/lib/ats-report";

// E058/US300: the report SHAPE and the two rules that read it moved to
// `lib/ats-report.ts` so the document review surface can read the same report
// without importing this component — one implementation of the check-id and
// detail-key rules (ADR-066), not two. Re-exported here so every existing
// `import type { ATSReport } from "@/components/cv/ATSChecksPanel"` keeps
// working.
export type { ATSCheck, ATSReport, PinnedFactReportEntry } from "@/lib/ats-report";

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

/**
 * Which slice of the report this mount renders.
 *
 * E058/US300 splits the panel's two jobs across the new review surface:
 * `"full"` is the pre-E058 form and is kept as the default so every existing
 * caller and test is unchanged; `"pins"` renders ONLY the per-pin fate section
 * (E056/ADR-077 cl. 3+5), which ADR-081 cl. 3 moves to the EDITING tab —
 * "fact pins stay outside the finding groups entirely". The keyword buckets and
 * the structure checks themselves are read by `lib/review-groups.ts` and
 * rendered as groups 1-4, so this component no longer renders them on the
 * document pages.
 */
export type ATSChecksPanelVariant = "full" | "pins";

export default function ATSChecksPanel({
  report,
  variant = "full",
}: {
  report: ATSReport;
  variant?: ATSChecksPanelVariant;
}) {
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
    usesLocalizedDetail(c) ? t(`checkDetails.${c.details_key}`, c.details_params!) : c.details;
  const failed = report.checks.filter((c) => c.status === "fail");
  // E042/US239 (ADR-051): a PASSING check can carry a non-null `details` string
  // (e.g. page-length passing "beyond the norm by choice"/"acceptable for senior
  // profiles"). These are informational, never failures — style and group them
  // separately so a pass-with-advisory never reads as a problem.
  const passingAdvisory = report.checks.filter((c) => c.status === "pass" && c.details);
  // E057/ADR-079 clause 4: a check that could not be evaluated on this
  // artefact — neither a pass nor a fail. Rendered in its own neutral list so
  // it is never silently absent (an absent check reads as a clean, complete
  // audit of something that was never examined — the #634 failure class).
  const notApplicable = report.checks.filter((c) => c.status === "not_applicable");
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

  // E056/ADR-077 clauses 3+5, extracted by E058/US300: ADR-081 cl. 3 moves
  // fact pins OUT of the finding groups and onto the editing tab, so this
  // block has to be renderable on its own (`variant="pins"`). Same markup,
  // same test ids — a relocation, not a re-write.
  // Per-pin presence measurement on THIS document — ship-and-report, never a
  // gate. Rendered whenever the report carries pin context, independent of
  // pass/fail status.
  const pinnedFactsBlock =
    pinnedFacts.length > 0 ? (
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
        ) : null;

  // ADR-081 cl. 3: on the document pages the pins render on the EDITING
  // tab, never inside a finding group, and never as a finding's remedy.
  if (variant === "pins") {
    return pinnedFactsBlock ? (
      <section
        data-testid="ats-panel-pins"
        aria-label={t("pinnedFacts.title")}
        className="rounded-xl border border-outline-variant surface-glass px-4 py-2.5"
      >
        {pinnedFactsBlock}
      </section>
    ) : null;
  }

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
            alongside them with informational — not failure — styling. not_applicable
            checks (E057/ADR-079 clause 4) render in the same list with a third,
            deliberately neutral treatment — never the success or critical palette. */}
        {(failed.length > 0 || passingAdvisory.length > 0 || notApplicable.length > 0) && (
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
            {/* E057/ADR-079 clause 4: not_applicable — deliberately neither the
                success nor the critical palette (mirrors TruthfulnessPanel's
                VERDICT_CHIP_CLASS treatment of its own not_applicable verdict:
                a genuinely neutral/informational chip, must not look like
                either a pass or a fail). */}
            {notApplicable.map((c) => (
              <li
                key={c.id}
                data-testid={`ats-notapplicable-${c.id}`}
                className="flex items-start gap-2 text-sm text-on-surface"
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative not-applicable glyph */}
                <span aria-hidden="true" className="mt-0.5 shrink-0 text-xs font-bold text-on-surface-variant">–</span>
                <span>
                  <span className="rounded-full border border-outline-variant px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-on-surface-variant">
                    {t("notApplicableLabel")}
                  </span>
                  {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between badge and label */}
                  {" "}
                  {t(labelKey(c.id))}
                  {c.details || c.details_key ? (
                    <span className="text-on-surface-variant">
                      {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative em-dash separator */}
                      {" — "}
                      {detailText(c)}
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        )}

        {pinnedFactsBlock}
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
                const failing = g.checks.filter((c) => c.status === "fail");
                const advisory = g.checks.filter((c) => c.status === "pass" && c.details);
                // E057/ADR-079 clause 4 (regression, #637): this used to read
                // `passed === g.checks.length` — a not_applicable check is
                // neither "pass" nor "fail", so a SINGLE not_applicable check
                // in an otherwise-clean group made `passed` fall short of
                // `g.checks.length` and rendered a red ✗ FAILURE glyph on a
                // band that never failed anything. "OK" now means "nothing in
                // this group failed" — not_applicable checks are excluded
                // from the denominator via `checkable`, below, the same
                // `checkable = total - not_applicable` idiom TruthfulnessPanel
                // already uses for its own not_applicable verdicts.
                const notApplicable = g.checks.filter((c) => c.status === "not_applicable");
                const checkable = g.checks.length - notApplicable.length;
                // Neutral when EVERY check in the group is not_applicable —
                // nothing was actually evaluated, so a green checkmark would
                // misread as "verified clean" (the #634 failure class).
                const groupStatus: "fail" | "pass" | "not_applicable" =
                  failing.length > 0 ? "fail" : checkable === 0 ? "not_applicable" : "pass";
                return (
                  <li
                    key={g.base}
                    data-testid={`ats-drawer-check-${g.base}`}
                    className="flex items-start gap-2 text-sm text-on-surface"
                  >
                    <span
                      aria-hidden="true"
                      className={`mt-0.5 shrink-0 text-xs font-bold ${
                        groupStatus === "fail"
                          ? "text-critical"
                          : groupStatus === "not_applicable"
                            ? "text-on-surface-variant"
                            : "text-success"
                      }`}
                    >
                      {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative pass/fail/not-applicable glyphs */}
                      {groupStatus === "fail" ? "✗" : groupStatus === "not_applicable" ? "–" : "✓"}
                    </span>
                    <span>
                      {t(`checks.${g.base}`)}
                      {checkable > 1 && (
                        <span className="text-on-surface-variant">
                          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space before count */}
                          {" "}
                          {t("groupCount", { passed, total: checkable })}
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
                      {/* not_applicable — its own note, named per check (never
                          silently absent), styled neutrally like the compact
                          card's treatment above. */}
                      {notApplicable.map((c) => (
                        <span
                          key={c.id}
                          data-testid={`ats-drawer-notapplicable-${c.id}`}
                          className="block text-xs text-on-surface-variant"
                        >
                          {t("notApplicableLabel")}
                          {c.details || c.details_key ? (
                            <span>
                              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative em-dash separator */}
                              {" — "}
                              {detailText(c)}
                            </span>
                          ) : null}
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
