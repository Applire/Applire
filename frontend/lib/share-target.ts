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
 * US229 (E040) — Web Share Target payload normalisation.
 *
 * ADR-050 amendment 2026-09-05 clause 4b: the manifest declares a `GET` share
 * target at {@link SHARE_TARGET_ACTION}, so Android hands us `title` / `text` /
 * `url` as query parameters and the Route Handler turns them into the Quick
 * Tailor deep link. The normalisation is load-bearing, not cosmetic: the Web
 * Share Target spec has three params and senders do not agree on them. Chrome's
 * "Share link" and the LinkedIn app both put the posting URL in **`text`**,
 * usually with the page title glued in front, and omit `url` — so a literal
 * `url` → `jd_url` binding prefills nothing on the most common real share
 * (Emma Journey-FMEA JF-E-Q.5).
 *
 * Nothing here starts work: the result is a prefill only, and
 * `POST /api/job/analyze` still needs the user's tap (clause 4c, JF-E-Q.6).
 */

/** The path the manifest's `share_target.action` points at. */
export const SHARE_TARGET_ACTION = "/share-target";

export interface SharePayload {
  title?: string | null;
  text?: string | null;
  url?: string | null;
}

export interface SharePrefill {
  /** Prefills the Quick Tailor **URL** tab. */
  jdUrl?: string;
  /** Prefills the Quick Tailor **text** tab. */
  jdText?: string;
}

// Deliberately narrow: only http(s) reaches the URL tab, because that tab's
// submit hits the JD scraper. Anything else (mailto:, javascript:, a bare
// domain) is prose as far as we are concerned and goes to the text tab, where
// the user can see and edit it before anything is sent.
const URL_IN_TEXT = /https?:\/\/[^\s<>"']+/i;
const TRAILING_PUNCTUATION = /[.,;:!?)\]]+$/;

function asHttpUrl(candidate: string | null | undefined): string | undefined {
  const value = (candidate ?? "").trim();
  if (!value) return undefined;
  // Match at the start only — an explicit `url` param that is not a link is not
  // a link with a link inside it.
  const match = /^https?:\/\/\S+$/i.exec(value);
  return match ? match[0].replace(TRAILING_PUNCTUATION, "") : undefined;
}

function findHttpUrl(haystack: string | null | undefined): string | undefined {
  const match = URL_IN_TEXT.exec(haystack ?? "");
  return match ? match[0].replace(TRAILING_PUNCTUATION, "") : undefined;
}

/**
 * Turn a raw share payload into the one field Quick Tailor should carry.
 *
 * Precedence: an explicit `url` param, then the first http(s) URL found in
 * `text`, then in `title`. With no URL anywhere the readable parts become the
 * text prefill (title first, deduplicated — senders routinely repeat the title
 * as the whole of `text`). An empty or whitespace-only share yields `{}`, and
 * the caller lands on the plain dashboard.
 */
export function normalizeSharePayload(payload: SharePayload): SharePrefill {
  const jdUrl = asHttpUrl(payload.url) ?? findHttpUrl(payload.text) ?? findHttpUrl(payload.title);
  if (jdUrl) return { jdUrl };

  const parts: string[] = [];
  for (const raw of [payload.title, payload.text, payload.url]) {
    const value = (raw ?? "").trim();
    if (value && !parts.includes(value)) parts.push(value);
  }
  const jdText = parts.join("\n\n");
  return jdText ? { jdText } : {};
}

/** The dashboard deep link a normalised share redirects to. */
export function shareTargetRedirectPath(prefill: SharePrefill): string {
  const params = new URLSearchParams();
  if (prefill.jdUrl) params.set("jd_url", prefill.jdUrl);
  else if (prefill.jdText) params.set("jd_text", prefill.jdText);
  const query = params.toString();
  return query ? `/dashboard?${query}` : "/dashboard";
}
