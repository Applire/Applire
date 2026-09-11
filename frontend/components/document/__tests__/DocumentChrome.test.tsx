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
 * E058/US299 — the dissolved `DocumentTopBar`, in its two new homes.
 *
 * These are the assertions the deleted `DocumentTopBar.test.tsx` carried,
 * re-pointed at `DocumentIdentityBar` (the panel header) and
 * `DocumentExportFooter` (the panel's pinned footer). Dissolving a bar is a
 * relocation: nothing it did may be lost, so nothing it was tested for is.
 */

import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { DocumentIdentityBar } from "../DocumentIdentityBar";
import { DocumentExportFooter } from "../DocumentExportFooter";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

describe("DocumentIdentityBar — the document switch and the language badge", () => {
  const BASE = { flowId: "flow-1", activeDoc: "cv" as "cv" | "cover-letter" };

  it("links to both documents with the ids the OQ and PQ specs drive", () => {
    render(withIntl(<DocumentIdentityBar {...BASE} />));
    expect(screen.getByTestId("document-nav-cv").getAttribute("href")).toBe("/flow/flow-1/cv");
    expect(screen.getByTestId("document-nav-cover-letter").getAttribute("href")).toBe(
      "/flow/flow-1/cover-letter",
    );
  });

  it("marks the active document", () => {
    render(withIntl(<DocumentIdentityBar {...BASE} activeDoc="cover-letter" />));
    expect(screen.getByTestId("document-nav-cover-letter").getAttribute("aria-current")).toBe("page");
    expect(screen.getByTestId("document-nav-cv").getAttribute("aria-current")).toBeNull();
  });

  // E054/US289 (JF-F-G2.2): the badge must not claim a language the generation
  // run never stamped.
  it("renders the ADR-038 language badge when the document carries a pinned language", () => {
    render(withIntl(<DocumentIdentityBar {...BASE} documentLanguage="en" />));
    expect(screen.getByTestId("document-language-badge")).toBeTruthy();
  });

  it("renders no badge for a legacy row without a pinned language", () => {
    render(withIntl(<DocumentIdentityBar {...BASE} documentLanguage={null} />));
    expect(screen.queryByTestId("document-language-badge")).toBeNull();
  });
});

describe("DocumentExportFooter — the exports, pinned to the panel", () => {
  it("wires the PDF button to onDownloadPdf", () => {
    const onDownloadPdf = vi.fn();
    render(withIntl(<DocumentExportFooter onDownloadPdf={onDownloadPdf} />));
    fireEvent.click(screen.getByTestId("document-download-btn"));
    expect(onDownloadPdf).toHaveBeenCalledOnce();
  });

  it("disables the PDF button while downloads are disabled", () => {
    render(withIntl(<DocumentExportFooter onDownloadPdf={vi.fn()} downloadDisabled />));
    expect((screen.getByTestId("document-download-btn") as HTMLButtonElement).disabled).toBe(true);
  });

  // US298 (E057 task 1.5): the .docx CTA renders ONLY when a handler is passed.
  it("omits the .docx CTA when no export handler is supplied", () => {
    render(withIntl(<DocumentExportFooter onDownloadPdf={vi.fn()} />));
    expect(screen.queryByTestId("document-download-docx-btn")).toBeNull();
  });

  it("wires the .docx CTA to onDownloadDocx when supplied", () => {
    const onDownloadDocx = vi.fn();
    render(withIntl(<DocumentExportFooter onDownloadPdf={vi.fn()} onDownloadDocx={onDownloadDocx} />));
    fireEvent.click(screen.getByTestId("document-download-docx-btn"));
    expect(onDownloadDocx).toHaveBeenCalledOnce();
  });

  it("disables the .docx CTA with the same flag as the PDF one", () => {
    render(
      withIntl(
        <DocumentExportFooter onDownloadPdf={vi.fn()} onDownloadDocx={vi.fn()} downloadDisabled />,
      ),
    );
    expect((screen.getByTestId("document-download-docx-btn") as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  // F-4b (founder ruling, 2026-09-11): the per-document signature override —
  // a nullable tri-state control ("Standard (an|aus) / mit / ohne").
  describe("the signature override control (F-4b)", () => {
    it("renders nothing extra when no signature prop is supplied", () => {
      render(withIntl(<DocumentExportFooter onDownloadPdf={vi.fn()} />));
      expect(screen.queryByTestId("signature-override-section")).toBeNull();
    });

    it("shows a disabled hint pointing at the profile card when unavailable", () => {
      render(
        withIntl(
          <DocumentExportFooter
            onDownloadPdf={vi.fn()}
            signature={{ available: false, kindDefaultOn: true, override: null, onChange: vi.fn() }}
          />,
        ),
      );
      expect(screen.queryByTestId("signature-override-control")).toBeNull();
      const hint = screen.getByTestId("signature-override-unavailable-hint");
      expect(hint.textContent).toContain("No signature on file");
      expect(hint.querySelector("a")?.getAttribute("href")).toBe("/profile");
    });

    it("marks the default option pressed when override is null", () => {
      render(
        withIntl(
          <DocumentExportFooter
            onDownloadPdf={vi.fn()}
            signature={{ available: true, kindDefaultOn: true, override: null, onChange: vi.fn() }}
          />,
        ),
      );
      expect(screen.getByTestId("signature-override-default").getAttribute("aria-pressed")).toBe(
        "true",
      );
      expect(screen.getByTestId("signature-override-on").getAttribute("aria-pressed")).toBe("false");
      expect(screen.getByTestId("signature-override-off").getAttribute("aria-pressed")).toBe("false");
    });

    it("words the default option from the kind default — on", () => {
      render(
        withIntl(
          <DocumentExportFooter
            onDownloadPdf={vi.fn()}
            signature={{ available: true, kindDefaultOn: true, override: null, onChange: vi.fn() }}
          />,
        ),
      );
      expect(screen.getByTestId("signature-override-default").textContent).toBe("Default (on)");
    });

    it("words the default option from the kind default — off", () => {
      render(
        withIntl(
          <DocumentExportFooter
            onDownloadPdf={vi.fn()}
            signature={{ available: true, kindDefaultOn: false, override: null, onChange: vi.fn() }}
          />,
        ),
      );
      expect(screen.getByTestId("signature-override-default").textContent).toBe("Default (off)");
    });

    it("marks the ON option pressed when override is true", () => {
      render(
        withIntl(
          <DocumentExportFooter
            onDownloadPdf={vi.fn()}
            signature={{ available: true, kindDefaultOn: false, override: true, onChange: vi.fn() }}
          />,
        ),
      );
      expect(screen.getByTestId("signature-override-on").getAttribute("aria-pressed")).toBe("true");
      expect(screen.getByTestId("signature-override-default").getAttribute("aria-pressed")).toBe(
        "false",
      );
    });

    it("marks the OFF option pressed when override is false", () => {
      render(
        withIntl(
          <DocumentExportFooter
            onDownloadPdf={vi.fn()}
            signature={{ available: true, kindDefaultOn: true, override: false, onChange: vi.fn() }}
          />,
        ),
      );
      expect(screen.getByTestId("signature-override-off").getAttribute("aria-pressed")).toBe("true");
    });

    it("raises onChange with true/false/null for the three options", () => {
      const onChange = vi.fn();
      render(
        withIntl(
          <DocumentExportFooter
            onDownloadPdf={vi.fn()}
            signature={{ available: true, kindDefaultOn: true, override: null, onChange }}
          />,
        ),
      );
      fireEvent.click(screen.getByTestId("signature-override-on"));
      fireEvent.click(screen.getByTestId("signature-override-off"));
      fireEvent.click(screen.getByTestId("signature-override-default"));
      expect(onChange.mock.calls).toEqual([[true], [false], [null]]);
    });

    it("disables the three options while saving", () => {
      render(
        withIntl(
          <DocumentExportFooter
            onDownloadPdf={vi.fn()}
            signature={{ available: true, kindDefaultOn: true, override: null, onChange: vi.fn(), saving: true }}
          />,
        ),
      );
      expect((screen.getByTestId("signature-override-on") as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByTestId("signature-override-off") as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByTestId("signature-override-default") as HTMLButtonElement).disabled).toBe(
        true,
      );
    });
  });
});
