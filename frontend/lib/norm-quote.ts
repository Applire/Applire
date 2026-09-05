// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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
 * The ADR-070 clause 1 quote fold, in the browser.
 *
 * ADR-081 clause 2 permits exactly ONE cross-producer suppression on the
 * document review surface: within group 1, an Oracle-flagged claim and a
 * `keywords.present_unsupported` term that normalise to the same string render
 * as one row citing both producers. The ADR names *the existing shared*
 * `_norm_quote` discipline (NFKC, apostrophe/quote/dash folds, whitespace
 * collapse, lowercase) — "reused, never reinvented".
 *
 * It cannot be literally reused: the canonical implementation is Python
 * (`backend/applire/services/scope_requirements.py:202`) and the carve-out runs
 * in the browser. This file is therefore a **port**, and ADR-066's
 * one-implementation-per-capability rule is not satisfied by a port. The
 * divergence risk is recorded as System-FMEA `SF-REVIEW.5` and DETECTED rather
 * than prevented: `tests/files/norm_quote_vectors.json` holds input→output
 * pairs produced by the Python fold, and both a pytest and a vitest assert
 * every pair. A change to either fold that is not made to the other reddens one
 * of the two suites.
 *
 * The step order below is the Python function's order, byte for byte, and must
 * stay that way — folding dashes after collapsing whitespace, or lowercasing
 * before NFKC, produces different output on real inputs.
 *
 * Known limit, stated rather than assumed: JavaScript's `\s` and Python's `\s`
 * are not the same character set (JS includes U+FEFF, Python includes
 * U+001C–U+001F and U+0085). NFKC folds the cases that occur in practice
 * (U+00A0 → space) before either regex runs; inputs carrying the remaining
 * exotic separators are outside what the vector file exercises.
 */

const APOSTROPHE_CHARS = "’ʼ‘‛´`"; // ’ ʼ ‘ ‛ ´ `
const QUOTE_CHARS = "“”„‟«»"; // “ ” „ ‟ « »

// `-` U+002D, the U+2010–U+2015 range (hyphen … horizontal bar), U+2212 minus.
const DASH_RE = /[-‐-―−]/g;
const WHITESPACE_RE = /\s+/g;

/** ADR-070 clause 1 — the quote-resolution fold. Port of `_norm_quote`. */
export function normQuote(s: string | null | undefined): string {
  let out = (s ?? "").normalize("NFKC");
  for (const ch of APOSTROPHE_CHARS) out = out.split(ch).join("'");
  for (const ch of QUOTE_CHARS) out = out.split(ch).join('"');
  out = out.replace(DASH_RE, " ");
  return out.replace(WHITESPACE_RE, " ").toLowerCase().trim();
}
