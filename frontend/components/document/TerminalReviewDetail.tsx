// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

import { useTranslations } from "next-intl";
import type { ATSCheck, CheckNote } from "@/lib/ats-report";

/**
 * R2-5 (founder ruling 2026-10-24, Hazelnut build 4) — the `terminal-review`
 * card in the user's words.
 *
 * ux-audit-2 B-2 (marcus-059/061, felix-018/055): the DE card printed the EN
 * diagnostic verbatim — "after 4 round(s)", "cv.role_budget", a role's UUID.
 * The backend now sends the state (`details_key` + `{count}`) and the items as
 * data (`notes`); this component says them in the UI language. Nothing here
 * prints a check id, a round count or a UUID: a code item without a sentence of
 * its own takes the generic line, never its EN `text`.
 *
 * Reviewer findings: the reviewer's own plain note (`plain`, in the document's
 * language) when it gave one; otherwise its words as written (ruling T-1).
 */

/** A catalog key may not contain "." (next-intl namespace separator). */
function catalogKey(key: string): string {
  return key.replace(/\./g, "-");
}

export function hasTerminalReviewCard(check: ATSCheck | undefined): check is ATSCheck {
  return Boolean(
    check &&
      check.id === "terminal-review" &&
      check.details_key &&
      check.details_params &&
      Array.isArray(check.notes),
  );
}

export function TerminalReviewDetail({
  check,
  documentKind,
}: {
  check: ATSCheck;
  documentKind: "cv" | "cover-letter";
}) {
  const tAts = useTranslations("ats");
  const t = useTranslations("ats.terminalReview");
  const notes = check.notes ?? [];
  const findings = notes.filter((n) => n.kind === "finding");
  const code = notes.filter((n) => n.kind === "code");
  const pipeline = notes.filter((n) => n.kind === "note");

  const codeLine = (n: CheckNote): string => {
    const subjects = n.subjects ?? [];
    const key = `code.${catalogKey(n.key ?? "")}`;
    if (!n.key || !t.has(key)) return t("code.generic");
    return t(key, {
      term: subjects[0] ?? "",
      terms: subjects.join(", "),
    });
  };
  const noteLine = (n: CheckNote): string => {
    const key = `note.${catalogKey(n.key ?? "")}`;
    if (!n.key || !t.has(key)) return t("note.generic");
    return t(key, { terms: (n.subjects ?? []).map((s) => `„${s}“`).join(" ") });
  };
  const findingLine = (n: CheckNote): string => {
    if (n.plain && n.plain.trim()) return n.plain;
    if (n.text && n.text.trim()) return n.text;
    return t("findingGeneric");
  };

  const list = (items: CheckNote[], line: (n: CheckNote) => string, testid: string) => (
    <ul data-testid={testid} className="mt-1 list-disc space-y-0.5 pl-4">
      {items.map((n, i) =>
        n.key === "more" ? (
          <li key={`more-${i}`} data-kind="more">
            {t("more", { count: n.count ?? 0 })}
          </li>
        ) : (
          <li key={`${n.kind}-${i}`} data-kind={n.kind} data-key={n.key ?? ""}>
            {line(n)}
          </li>
        ),
      )}
    </ul>
  );

  return (
    <span data-testid="terminal-review-detail" data-state={check.details_key ?? ""} className="block">
      <span className="block">{tAts(`checkDetails.${check.details_key}`, check.details_params ?? undefined)}</span>
      {findings.length > 0 && list(findings, findingLine, "terminal-review-findings")}
      {code.length > 0 && (
        <>
          <span className="mt-1.5 block font-semibold text-on-surface">
            {documentKind === "cv" ? t("codeHeadingCv") : t("codeHeadingLetter")}
          </span>
          {list(code, codeLine, "terminal-review-code")}
        </>
      )}
      {pipeline.length > 0 && list(pipeline, noteLine, "terminal-review-notes")}
    </span>
  );
}
