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

// Pure conversion between a server UTC ISO instant (`Application.deadline`)
// and the wall-clock string an `<input type="datetime-local">` expects
// ("YYYY-MM-DDTHH:mm", no timezone). Fixes #164: the deleted old page code
// did `data.deadline.slice(0, 16)` on load (took the UTC digits verbatim
// into a local input) but `new Date(value).toISOString()` on save
// (interprets the SAME digits as local time) — each round trip sheared the
// UTC offset off the deadline. Both directions here go through the Date
// object's local getters/constructor, so the round trip is symmetric.

/**
 * Server UTC ISO instant -> local `datetime-local` input value.
 * Example: "2026-08-15T07:30:00Z" -> "2026-08-15T09:30" in CEST (UTC+2).
 * Returns "" for an unparseable/empty input so callers can bind it directly
 * to a controlled input's `value` without an extra guard.
 */
export function isoUtcToLocalInput(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * `datetime-local` input value -> server UTC ISO instant. Inverse of
 * `isoUtcToLocalInput`: `new Date(v)` parses a bare "YYYY-MM-DDTHH:mm" string
 * as LOCAL time (no offset in the string), so `.toISOString()` converts it
 * to the correct UTC instant.
 * Returns "" for an empty/unparseable input (mirroring `isoUtcToLocalInput`)
 * instead of letting `.toISOString()` throw a RangeError; callers that need
 * the backend's clear semantics map "" to an explicit `null` themselves.
 */
export function localInputToIsoUtc(v: string): string {
  if (!v) return "";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString();
}
