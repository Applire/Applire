"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The one-line instance health on Administration → Übersicht (#694; mock
 * admin-dashboard §1/§3/§4): verdict + count, the components that are not ok as
 * chips, a link to Monitoring for the facts. Component and status words reuse
 * the approved `ops.*` catalog of the operator panel.
 */

import { useLocale, useTranslations } from "next-intl";

import type { DashboardHealth } from "@/lib/api/admin";
import { CardLink } from "../common";

const KNOWN_COMPONENTS = new Set(["database", "migrations", "retention", "disk", "backup", "provider", "errors"]);
const KNOWN_STATUS = new Set(["ok", "degraded", "down", "unknown"]);

export function HealthStrip({ health }: { health: DashboardHealth }) {
  const t = useTranslations("adminDashboard");
  const tOps = useTranslations("ops");
  const locale = useLocale();
  const bad = health.components.filter((c) => c.status !== "ok");
  const kind = health.status === "down" ? "critical" : health.status === "ok" ? "ok" : "warning";
  const verdict =
    kind === "ok" ? t("healthOk") : kind === "critical" ? t("healthDown", { count: bad.length }) : t("healthDegraded", { count: bad.length });
  const box =
    kind === "ok"
      ? "bg-white shadow-soft"
      : kind === "critical"
        ? "border border-critical/35 bg-critical-container"
        : "border border-warning/45 bg-warning-container";
  const dot = kind === "ok" ? "bg-success" : kind === "critical" ? "bg-critical" : "bg-warning";
  const time = health.checked_at
    ? new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(new Date(health.checked_at))
    : null;
  const label = (name: string) => (KNOWN_COMPONENTS.has(name) ? tOps(`component.${name}`) : name);
  const status = (s: string) => (KNOWN_STATUS.has(s) ? tOps(`status.${s}`) : s);

  return (
    <div data-testid="admin-health-strip" data-status={health.status} className={`flex flex-wrap items-center gap-x-2.5 gap-y-2 rounded-lg px-4 py-3.5 text-[13.5px] ${box}`}>
      <span aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} />
      <b className="font-bold text-on-surface">{t("healthTitle")}</b>
      <span data-testid="admin-health-verdict">{verdict}</span>
      {time && <span className="text-[12px] text-on-surface-variant">{t("checkedAt", { time })}</span>}
      <span className="ml-auto">
        <CardLink href="/admin/monitoring" testId="admin-health-details">
          {t("healthDetails")}
        </CardLink>
      </span>
      {bad.length > 0 && (
        <div className="flex w-full flex-wrap gap-1.5">
          {bad.map((c) => (
            <span
              key={c.name}
              data-testid={`admin-health-chip-${c.name}`}
              className="inline-flex items-center gap-1.5 rounded-full border border-outline-variant bg-white/75 px-2.5 py-0.5 text-[12px] text-on-surface"
            >
              <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${c.status === "down" ? "bg-critical" : "bg-warning"}`} />
              {[label(c.name), status(c.status)].join(" — ")}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
