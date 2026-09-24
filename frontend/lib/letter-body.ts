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
 * D-2 (ADR-090 delivery run): the letter body the user actually has.
 *
 * `letter_data` is the generated letter, RAW; a saved body — a manual edit or
 * a *Take it out for me* rewrite — lives in `section_overrides.body`. The body
 * editor must start from the EFFECTIVE body, or saving it writes the
 * pre-override text back and silently undoes the earlier change.
 *
 * The override is split exactly as the backend renders it
 * (`services/cover_letter.py::_apply_section_overrides`): CRLF/CR → LF, split
 * on a blank line (`\n[ \t]*\n`), trim, drop empties. Re-joined with a blank
 * line, which is the editor's own paragraph separator.
 */

interface LetterBodyData {
  body?: { paragraphs?: string[] };
}

export function overrideParagraphs(content: string): string[] {
  const normalised = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const paras = normalised
    .split(/\n[ \t]*\n/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
  return paras.length > 0 ? paras : [content];
}

export function effectiveLetterBody(
  letterData: LetterBodyData | null | undefined,
  sectionOverrides: Record<string, unknown> | null | undefined,
): string {
  const override = sectionOverrides?.body;
  if (typeof override === "string") return overrideParagraphs(override).join("\n\n");
  return letterData?.body?.paragraphs?.join("\n\n") ?? "";
}
