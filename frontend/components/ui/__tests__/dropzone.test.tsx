// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, expect, it } from "vitest";

import de from "@/messages/de.json";
import en from "@/messages/en.json";

import { CV_UPLOAD_ACCEPT } from "../dropzone";

/**
 * Frontend collector #604 — "the upload dropzone advertises 'PDF, DOCX, DOC'
 * only; plain text / .md and images (OCR) are also accepted per the backend."
 *
 * The defect was a mismatch between three things that nothing kept in step: the
 * file picker's `accept`, the sentence under the dropzone, and what
 * `services/cv_parser.py::detect_format` actually parses. Fixing the sentence
 * alone would have produced the worse mismatch — text promising a format the
 * picker filters out — so this pins all three against each other.
 *
 * The backend list is transcribed from `detect_format` (PDF; .docx/.doc; the
 * seven image extensions; text/* plus .txt/.md) and the `/api/profile/upload`
 * docstring. If that list changes, this test is where it must be re-read.
 */
const BACKEND_PARSES = {
  pdf: [".pdf"],
  docx: [".docx", ".doc"],
  image: [".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"],
  text: [".txt", ".md"],
};

describe("CV upload accept list (#604)", () => {
  const accepted = CV_UPLOAD_ACCEPT.split(",");

  it("offers every extension the backend can parse", () => {
    for (const [format, extensions] of Object.entries(BACKEND_PARSES)) {
      for (const ext of extensions) {
        expect(accepted, `${format}: ${ext} missing from the picker`).toContain(ext);
      }
    }
  });

  it("offers nothing the backend cannot parse", () => {
    const known = Object.values(BACKEND_PARSES).flat();
    for (const ext of accepted) {
      expect(known, `${ext} is offered but not parsed`).toContain(ext);
    }
  });

  it.each([
    ["de", de],
    ["en", en],
  ])("the %s format hint names the formats it now accepts", (_locale, catalog) => {
    // The sentence is prose, not a list, so assert the format WORDS rather than
    // a shape — the point of the collector line is that a user reading it learns
    // that plain text and images are allowed.
    const hint = (catalog as { dropzone: { formatHint: string } }).dropzone.formatHint.toUpperCase();
    for (const token of ["PDF", "DOCX", "TXT", "MD"]) {
      expect(hint, `${token} not advertised`).toContain(token);
    }
    expect(hint).toMatch(/JPG|PNG|BILD|IMAGE/);
  });

  it.each([
    ["de", de],
    ["en", en],
  ])("the %s profile-import blurb does the same", (_locale, catalog) => {
    const formats = (
      catalog as { profileImport: { formats: string } }
    ).profileImport.formats.toUpperCase();
    for (const token of ["PDF", "DOCX", "TXT", "MD", "ZIP"]) {
      expect(formats, `${token} not advertised`).toContain(token);
    }
  });
});
