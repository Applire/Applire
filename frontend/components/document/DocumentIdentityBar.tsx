"use client";

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

import Link from "next/link";
import { useTranslations } from "next-intl";
import { DocumentLanguageBadge } from "@/components/document/DocumentLanguageBadge";

interface DocumentIdentityBarProps {
  flowId: string;
  activeDoc: "cv" | "cover-letter";
  /**
   * E054/US289 (FMEA JF-F-G2.2): the active document's PINNED language.
   * null/undefined (legacy rows, still generating) renders no badge — the bar
   * must not claim a language the generation run never stamped.
   */
  documentLanguage?: "de" | "en" | null;
}

/**
 * The document's identity: which document you are looking at, and in which
 * language it was written.
 *
 * E058/US299, ADR-081 clause 1 — `DocumentTopBar` is dissolved and this is the
 * half that moves into the workspace panel's HEADER (the exports become its
 * pinned footer, see `DocumentExportFooter`). The markup and the test ids are
 * carried over unchanged so the OQ and PQ specs that drive the document switch
 * (`document-nav-cv`, `document-nav-cover-letter`) keep working — dissolving a
 * bar is a relocation, not a re-write of what it does.
 *
 * Rendered on a dark (navy) ground inside the panel header, so the inactive
 * pill text is white-dimmed rather than `on-surface-variant`.
 */
export function DocumentIdentityBar({
  flowId,
  activeDoc,
  documentLanguage = null,
}: DocumentIdentityBarProps) {
  const t = useTranslations("document");

  const items = [
    { id: "cv", href: `/flow/${flowId}/cv`, label: t("navCv"), testid: "document-nav-cv" },
    {
      id: "cover-letter",
      href: `/flow/${flowId}/cover-letter`,
      label: t("navCoverLetter"),
      testid: "document-nav-cover-letter",
    },
  ] as const;

  return (
    <div
      className="flex items-center gap-2 min-w-0"
      data-testid="document-identity-bar"
    >
      <div
        className="flex items-center gap-1 rounded-full bg-white/10 p-0.5"
        role="tablist"
        aria-label={t("navAriaLabel")}
      >
        {items.map((it) => {
          const active = it.id === activeDoc;
          return (
            <Link
              key={it.id}
              href={it.href}
              role="tab"
              aria-current={active ? "page" : undefined}
              aria-selected={active}
              data-testid={it.testid}
              className={`px-3 py-1 rounded-full text-xs font-heading font-semibold transition-colors ${
                active ? "bg-white text-primary shadow-sm" : "text-white/70 hover:text-white"
              }`}
            >
              {it.label}
            </Link>
          );
        })}
      </div>
      {documentLanguage && <DocumentLanguageBadge lang={documentLanguage} />}
    </div>
  );
}
