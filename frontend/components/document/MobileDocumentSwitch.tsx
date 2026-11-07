"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import Link from "next/link";
import { useTranslations } from "next-intl";
import { DocumentLanguageBadge } from "@/components/document/DocumentLanguageBadge";

interface MobileDocumentSwitchProps {
  flowId: string;
  activeDoc: "cv" | "cover-letter";
  /** The active document's PINNED language; null renders no badge. */
  documentLanguage?: "de" | "en" | null;
}

/**
 * 789:252 (Hazelnut build 7, WP-F) — the phone's way between CV and letter.
 *
 * Below `md` the workspace panel (and with it `DocumentIdentityBar`, the only
 * "Anschreiben" link) is `display: none` — Emma reached the letter on the
 * phone only by typing the URL. This is the SAME switch, on a light ground,
 * above the preview and only below `md`. Its own test ids
 * (`document-nav-mobile-*`): the desktop bar's ids stay unique on the page,
 * so the specs that drive `document-nav-cover-letter` keep resolving to one
 * element. With no letter yet, "Anschreiben" opens the letter page's empty
 * state, which offers "Anschreiben erstellen".
 */
export function MobileDocumentSwitch({ flowId, activeDoc, documentLanguage = null }: MobileDocumentSwitchProps) {
  const t = useTranslations("document");
  const items = [
    { id: "cv", href: `/flow/${flowId}/cv`, label: t("navCv"), testid: "document-nav-mobile-cv" },
    {
      id: "cover-letter",
      href: `/flow/${flowId}/cover-letter`,
      label: t("navCoverLetter"),
      testid: "document-nav-mobile-cover-letter",
    },
  ] as const;

  return (
    <div
      className="md:hidden flex items-center justify-between gap-2 pb-2"
      data-testid="document-switch-mobile"
    >
      <div
        className="flex items-center gap-1 rounded-full bg-surface-container p-0.5"
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
              className={`px-4 py-1.5 rounded-full text-sm font-heading font-semibold transition-colors ${
                active ? "bg-primary text-white shadow-sm" : "text-on-surface-variant hover:text-on-surface"
              }`}
            >
              {it.label}
            </Link>
          );
        })}
      </div>
      {documentLanguage && <DocumentLanguageBadge lang={documentLanguage} testid="document-language-badge-mobile" />}
    </div>
  );
}
