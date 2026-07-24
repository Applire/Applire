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

/**
 * Parse the filename a download's `Content-Disposition` response header
 * actually names, so the browser's Save-As dialog offers the SAME name the
 * backend computed (issue #246 / NEW-5).
 *
 * The cover-letter PDF download used to hardcode `a.download =
 * "anschreiben.pdf"` regardless of what the server sent — silently
 * overriding the jd_language-aware filename (`get_cover_letter_pdf_filename`,
 * PR #242) whenever the letter's output language was English. The CV
 * download path already reads the header correctly; this is the same idea,
 * generalized to also understand the RFC 5987 `filename*=` form.
 *
 * Prefers `filename*=` (RFC 5987 — percent-encoded, charset-tagged) over the
 * plain `filename="…"` form when both are present, per RFC 6266 §4.3. Both
 * this app's actual filenames are ASCII-safe today (`filename_part` strips
 * accents), so `filename*=` is not currently emitted — this simply future-
 * proofs the parser rather than leaving the possibility silently unhandled.
 */
export function extractFilenameFromContentDisposition(
  header: string | null | undefined,
): string | null {
  if (!header) return null;

  const starMatch = header.match(/filename\*\s*=\s*([^;]+)/i);
  if (starMatch) {
    // filename*=charset'lang'percent-encoded-value
    const value = starMatch[1].trim();
    const parts = value.split("'");
    const encoded = parts.length >= 3 ? parts.slice(2).join("'") : value;
    try {
      const decoded = decodeURIComponent(encoded);
      if (decoded) return decoded;
    } catch {
      // fall through to the plain form below
    }
  }

  const plainMatch = header.match(/filename\s*=\s*"?([^";]+)"?/i);
  if (plainMatch) {
    const value = plainMatch[1].trim();
    if (value) return value;
  }

  return null;
}
