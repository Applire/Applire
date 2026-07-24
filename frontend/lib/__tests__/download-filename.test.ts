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
 * issue #246 (NEW-5) — the cover-letter download hardcoded
 * `a.download = "anschreiben.pdf"`, silently overriding the server's real
 * Content-Disposition filename (which already carries the correct
 * jd_language-derived suffix, e.g. "…_Cover-Letter.pdf" for an English
 * letter — the #241/PR#242 backend fix). This is the parser the download
 * handler uses to read the server's actual filename instead.
 */
import { describe, it, expect } from "vitest";
import { extractFilenameFromContentDisposition } from "../download-filename";

describe("extractFilenameFromContentDisposition", () => {
  it("extracts a plain quoted filename= filename", () => {
    expect(
      extractFilenameFromContentDisposition(
        'attachment; filename="Max-Muster_Acme_Lead-Engineer_Cover-Letter.pdf"',
      ),
    ).toBe("Max-Muster_Acme_Lead-Engineer_Cover-Letter.pdf");
  });

  it("extracts an unquoted filename= filename", () => {
    expect(
      extractFilenameFromContentDisposition("attachment; filename=anschreiben.pdf"),
    ).toBe("anschreiben.pdf");
  });

  it("prefers the RFC 5987 filename*= form when both are present, decoding percent-escapes", () => {
    expect(
      extractFilenameFromContentDisposition(
        'attachment; filename="anschreiben.pdf"; filename*=UTF-8\'\'Max-M%C3%BCller_Cover-Letter.pdf',
      ),
    ).toBe("Max-Müller_Cover-Letter.pdf");
  });

  it("returns null when the header has no filename directive", () => {
    expect(extractFilenameFromContentDisposition("attachment")).toBeNull();
  });

  it("returns null for a null/missing header (no Content-Disposition sent)", () => {
    expect(extractFilenameFromContentDisposition(null)).toBeNull();
    expect(extractFilenameFromContentDisposition(undefined)).toBeNull();
  });

  it("returns null for an empty string", () => {
    expect(extractFilenameFromContentDisposition("")).toBeNull();
  });
});
