"use client";

// Copyright (C) 2026 Tobias Rosenbaum
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

/**
 * UAT-2b/2c (founder UAT 2026-11-08, amends ADR-064) — the one lead-in above
 * an interview question's answer ideas, and the button label of each idea.
 *
 * - A button shows the idea's `ask` (a du-question naming the profile anchor
 *   and the asked concept) and falls back to its `text`; a click always
 *   inserts the `text` (the first-person statement) — the call sites keep that.
 * - The lead-in names the concepts the backend found NO literal mention of
 *   (`choice_unmentioned`, computed deterministically by the producer — never
 *   LLM-written). Without them it is the plain lead-in; without any `ask`
 *   (an older turn, or a draft without asks) it stays the old hint, because
 *   "one of these questions" would then sit above statements.
 *
 * Used by the interview page, the gaps page and the LiabilityPanel — the three
 * surfaces of the same session generator.
 */
import { useLocale, useTranslations } from "next-intl";

/** Quote each concept the locale's way and join them as a disjunction:
 *  de „A“ · „A“ oder „B“ · „A“, „B“ oder „C“; en ‘A’ · ‘A’ or ‘B’ · ‘A’, ‘B’, or ‘C’. */
export function quoteConceptList(concepts: string[], locale: string): string {
  const [open, close] = locale.startsWith("de") ? ["„", "“"] : ["‘", "’"];
  const quoted = concepts.map((c) => `${open}${c.trim()}${close}`);
  try {
    return new Intl.ListFormat(locale, { style: "long", type: "disjunction" }).format(quoted);
  } catch {
    return quoted.join(", ");
  }
}

/** The label of choice `i`: its ask when the backend sent one, else its text. */
export function choiceButtonLabel(
  choices: string[],
  asks: (string | null)[] | null | undefined,
  i: number,
): string {
  const ask = asks?.[i];
  return typeof ask === "string" && ask.trim() ? ask : choices[i];
}

/** True when at least one choice carries an ask (the buttons read as questions). */
export function hasAsks(asks: (string | null)[] | null | undefined): boolean {
  return !!asks?.some((a) => typeof a === "string" && a.trim());
}

export function ChoiceLeadIn({
  namespace,
  asks,
  unmentioned,
}: {
  namespace: "gaps" | "interview";
  asks: (string | null)[] | null | undefined;
  unmentioned: string[] | null | undefined;
}) {
  const t = useTranslations(namespace);
  const locale = useLocale();
  const concepts = (unmentioned ?? []).filter((c) => c && c.trim());
  let text: string;
  if (!hasAsks(asks)) text = t("choiceCardHint");
  else if (concepts.length > 0) text = t("choiceLeadUnmentioned", { concepts: quoteConceptList(concepts, locale) });
  else text = t("choiceLead");
  return (
    <p data-testid="choice-lead-in" className="text-xs text-gray-400">
      {text}
    </p>
  );
}
