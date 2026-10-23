// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Display casing for a person's name (UX audit 2026-10-20: a CV headed
 * "STEFAN BRANDT" greeted the user as "Willkommen zurück, STEFAN").
 *
 * Only a token that is ENTIRELY upper-case and has more than one letter is
 * re-cased; everything else stays exactly as given ("McDonald", "von Weber",
 * "Daniel Kovač", initials like "J."). Hyphens, apostrophes and dots start a
 * new capital: ANNA-LENA → Anna-Lena, O'NEIL → O'Neil.
 */

const LETTER = /\p{L}/gu;
const SEGMENT_SPLIT = /([-'’.])/;

function isShouting(token: string): boolean {
  const letters = token.match(LETTER);
  return !!letters && letters.length > 1 && token === token.toUpperCase() && token !== token.toLowerCase();
}

function recase(token: string): string {
  return token
    .split(SEGMENT_SPLIT)
    .map((seg) => (seg.length ? seg.charAt(0).toUpperCase() + seg.slice(1).toLowerCase() : seg))
    .join("");
}

export function displayName(raw: string | null | undefined): string | null {
  const name = (raw ?? "").trim();
  if (!name) return null;
  return name
    .split(/(\s+)/)
    .map((tok) => (isShouting(tok) ? recase(tok) : tok))
    .join("");
}

/** The first name token of the display name, or null. */
export function firstNameOf(raw: string | null | undefined): string | null {
  const name = displayName(raw);
  if (!name) return null;
  return name.split(/\s+/)[0] || null;
}
