"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Small building blocks shared by the Epic C admin pages (c2 mocks): the card,
 * the source badge (ADR-093 cl. 3 — where a setting's value comes from), the
 * "for admins" 403 state and the load-failed line. Material-3 tokens only
 * (applire-i18n: no shadcn names).
 */

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Lock } from "lucide-react";

import type { SettingSource } from "@/lib/api/admin";

/** The em dash for "no value" cells — a symbol, not text (kept out of JSX literals). */
export const NONE = "\u2014";

export function AdminCard({
  title,
  action,
  children,
  testId,
  className = "",
}: {
  title?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  testId?: string;
  className?: string;
}) {
  return (
    <section data-testid={testId} className={`min-w-0 rounded-lg bg-white p-4 shadow-soft sm:p-5 ${className}`}>
      {(title || action) && (
        <div className="mb-3 flex items-center justify-between gap-3">
          {title && <h3 className="font-manrope text-[15px] font-bold text-on-surface">{title}</h3>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function CardLink({ href, children, testId }: { href: string; children: React.ReactNode; testId?: string }) {
  return (
    <Link href={href} data-testid={testId} className="whitespace-nowrap text-[12.5px] font-semibold text-teal hover:underline">
      {children}
    </Link>
  );
}

/** `.env` / `hier gesetzt` / `Standard` — the panel always says which value wins. */
export function SourceBadge({ source, testId }: { source: SettingSource; testId?: string }) {
  const t = useTranslations("adminSettings");
  const label = source === "panel" ? t("sourcePanel") : source === "env" ? t("sourceEnv") : t("sourceDefault");
  const title = source === "panel" ? t("sourcePanelTitle") : source === "env" ? t("sourceEnvTitle") : undefined;
  const cls =
    source === "panel"
      ? "border-outline-variant bg-primary-container text-primary"
      : source === "env"
        ? "border-outline-variant bg-surface-container text-on-surface-variant"
        : "border-dashed border-outline-variant bg-white text-on-surface-variant";
  return (
    <span
      data-testid={testId}
      data-source={source}
      title={title}
      className={`inline-flex items-center whitespace-nowrap rounded border px-1.5 font-mono text-[10.5px] font-semibold leading-[1.6] ${cls}`}
    >
      {label}
    </span>
  );
}

export function AdminForbidden() {
  const t = useTranslations("adminNav");
  return (
    <div data-testid="admin-forbidden" className="mx-auto max-w-3xl rounded-lg bg-white px-6 py-10 text-center shadow-soft">
      <Lock className="mx-auto h-10 w-10 text-on-surface-variant/60" aria-hidden />
      <h2 className="mt-3 font-manrope text-xl font-bold text-on-surface">{t("forbiddenTitle")}</h2>
      <p className="mx-auto mb-5 mt-2 max-w-md text-[14px] leading-relaxed text-on-surface-variant">{t("forbiddenBody")}</p>
      <Link
        href="/dashboard"
        data-testid="admin-forbidden-back"
        className="inline-flex h-10 items-center rounded-lg bg-teal px-4 text-[13px] font-semibold text-white shadow-soft"
      >
        {t("forbiddenBack")}
      </Link>
    </div>
  );
}

export function LoadFailed({ onRetry, testId }: { onRetry?: () => void; testId?: string }) {
  const t = useTranslations("adminNav");
  const tc = useTranslations("common");
  return (
    <div data-testid={testId} className="flex items-center gap-3 rounded-lg bg-white p-4 text-[13px] text-on-surface-variant shadow-soft">
      <span>{t("loadFailed")}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="font-semibold text-teal underline underline-offset-2">
          {tc("retry")}
        </button>
      )}
    </div>
  );
}

export function Loading() {
  const tc = useTranslations("common");
  return <div className="p-4 text-[13px] text-on-surface-variant">{tc("loading")}</div>;
}
