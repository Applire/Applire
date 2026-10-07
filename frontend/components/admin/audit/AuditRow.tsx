"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * One audit row as text (S-11; mock admin-audit.html). Labels and detail words
 * come from `adminAudit.*`; raw values (roles, booleans) are translated through
 * `auditValueKey`, everything else (setting keys, provider ids) is shown
 * verbatim. Metadata only — no IP (RD-11), no secret value (contract §3.2).
 */

import { useLocale, useTranslations } from "next-intl";

import type { AuditEventItem } from "@/lib/api/admin";
import { auditActionLabel, auditDetailParts, auditValueKey } from "@/lib/api/admin-format";

export function useAuditText() {
  const t = useTranslations("adminAudit");
  const tRoot = useTranslations();
  const locale = useLocale();
  const word = (v: string | number) => {
    const k = typeof v === "string" ? auditValueKey(v) : null;
    return k ? tRoot(k) : String(v);
  };
  return {
    label(e: AuditEventItem): string {
      const l = auditActionLabel(e.action, e.detail);
      return t(l.key, l.params);
    },
    actor(e: AuditEventItem): string {
      if (e.actor_email) return e.actor_email;
      return e.actor_user_id ? t("deletedPerson") : t("actorSystem");
    },
    target(e: AuditEventItem): string | null {
      if (e.target_email) return e.target_email;
      return e.target_user_id ? t("deletedPerson") : null;
    },
    detail(e: AuditEventItem): string {
      return auditDetailParts(e.action, e.detail)
        .map((p) => {
          const params: Record<string, string | number> = {};
          for (const [k, v] of Object.entries(p.params ?? {})) params[k] = k === "count" || k === "setting" ? v : word(v);
          return t(p.key, params);
        })
        .join(" · ");
    },
    when(e: AuditEventItem): string {
      return new Intl.DateTimeFormat(locale, { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(e.at));
    },
  };
}

/** The compact card form (overview "latest" list and the 390 px audit list). */
export function AuditRowCompact({ event: e }: { event: AuditEventItem }) {
  const a = useAuditText();
  const target = a.target(e);
  const actor = a.actor(e);
  const who = target && target !== actor ? [actor, target].join(" → ") : actor;
  const detail = a.detail(e);
  return (
    <div data-testid="admin-audit-card" data-action={e.action} className="border-b border-outline-variant/50 py-2.5 text-[13px] leading-snug last:border-0">
      <div className="flex justify-between gap-2">
        <b className="font-semibold text-on-surface">{a.label(e)}</b>
        <span className="whitespace-nowrap text-[12px] text-on-surface-variant">{a.when(e)}</span>
      </div>
      <div className="break-all text-[12px] text-on-surface-variant">{who}</div>
      {detail && <div className="text-[12.5px] text-on-surface">{detail}</div>}
    </div>
  );
}
