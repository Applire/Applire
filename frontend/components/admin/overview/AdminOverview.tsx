"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Administration → Übersicht (#694; c2 mock admin-dashboard.html). The admin's
 * landing: instance health in one line, people / usage / failed jobs / version
 * tiles, the heaviest AI users, failed jobs (kind, reason code, person, time —
 * never content), the three instance settings with their source, and the latest
 * audit rows. Metadata only (S-4, RD-6).
 */

import { useLocale, useTranslations } from "next-intl";

import { formatWhen } from "@/components/account/format";
import { UpgradeNotice } from "@/components/dashboard/UpgradeNotice";
import { getAuditPage, findSetting, SETTING_KEYS, type AdminDashboardResponse, type InstanceSettingsResponse, type UsageUserRow, type AdminUsageResponse } from "@/lib/api/admin";
import { failedKindKey, failedReason } from "@/lib/api/admin-format";
import { useAdminDashboard, useAdminResource, useAdminUsage, useInstanceSettings } from "@/lib/api/admin-hooks";
import { AdminCard, AdminForbidden, CardLink, LoadFailed, Loading, SourceBadge, NONE } from "../common";
import { AuditRowCompact } from "../audit/AuditRow";
import { HealthStrip } from "./HealthStrip";

function Tile({ label, big, small, link, testId, tone }: { label: string; big: string; small?: React.ReactNode; link?: React.ReactNode; testId: string; tone?: "warning" }) {
  return (
    <div data-testid={testId} className="flex min-w-0 flex-col gap-1.5 rounded-lg bg-white p-4 shadow-soft">
      <span className="text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">{label}</span>
      <span className={`font-manrope text-2xl font-extrabold leading-tight ${tone === "warning" ? "text-gold-dim" : "text-on-surface"}`}>{big}</span>
      {small && <span className="text-[12.5px] leading-snug text-on-surface-variant">{small}</span>}
      {link && <span className="mt-auto pt-1">{link}</span>}
    </div>
  );
}

function TopUsers({ usage }: { usage: AdminUsageResponse }) {
  const t = useTranslations("adminDashboard");
  const locale = useLocale();
  const nf = new Intl.NumberFormat(locale);
  const rows: UsageUserRow[] = usage.users.filter((u) => u.totals.total_tokens > 0).slice(0, 5);
  const max = Math.max(1, ...rows.map((r) => r.totals.total_tokens), usage.unattributed.total_tokens);
  const bar = (n: number, grey = false) => (
    <span className="h-1.5 w-[70px] shrink-0 overflow-hidden rounded-full bg-surface-container sm:w-[140px]">
      <span className={`block h-full ${grey ? "bg-on-surface-variant/40" : "bg-teal"}`} style={{ width: `${Math.max(2, Math.round((n / max) * 100))}%` }} />
    </span>
  );
  return (
    <AdminCard testId="admin-overview-top-users" title={t("topUsersTitle")} action={<CardLink href="/admin/usage">{t("usageLink")}</CardLink>}>
      {rows.length === 0 && usage.unattributed.total_tokens === 0 ? (
        <p className="text-[13px] text-on-surface-variant">{t("topUsersEmpty")}</p>
      ) : (
        <div>
          {rows.map((r) => (
            <div key={r.user_id} data-testid="admin-overview-top-user" className="flex items-center gap-3 border-b border-outline-variant/50 py-2 text-[13px] last:border-0">
              <span className="min-w-0 flex-1 truncate">{r.email}</span>
              {bar(r.totals.total_tokens)}
              <span className="whitespace-nowrap tabular-nums text-on-surface-variant">{nf.format(r.totals.total_tokens)}</span>
            </div>
          ))}
          {usage.unattributed.total_tokens > 0 && (
            <div className="flex items-center gap-3 py-2 text-[13px]" title={t("unattributedHint")}>
              <span className="min-w-0 flex-1 truncate text-on-surface-variant">{t("unattributed")}</span>
              {bar(usage.unattributed.total_tokens, true)}
              <span className="whitespace-nowrap tabular-nums text-on-surface-variant">{nf.format(usage.unattributed.total_tokens)}</span>
            </div>
          )}
        </div>
      )}
    </AdminCard>
  );
}

function FailedJobs({ data }: { data: AdminDashboardResponse }) {
  const t = useTranslations("adminDashboard");
  const ta = useTranslations("account");
  const locale = useLocale();
  const items = data.failed_jobs.items;
  const when = (iso: string | null) => (iso ? formatWhen(iso, locale, ta) : NONE);
  const reason = (code: string | null) => {
    const r = failedReason(code);
    return t(r.key, r.params);
  };
  return (
    <AdminCard testId="admin-overview-failed" title={t("failedJobsTitle")}>
      {items.length === 0 ? (
        <p className="text-[13px] text-on-surface-variant">{t("failedJobsEmpty")}</p>
      ) : (
        <>
          <table className="hidden w-full text-left text-[13px] sm:table">
            <thead>
              <tr className="border-b border-outline-variant text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">
                <th className="px-2 py-2">{t("colWhen")}</th>
                <th className="px-2 py-2">{t("colKind")}</th>
                <th className="px-2 py-2">{t("colReason")}</th>
                <th className="px-2 py-2">{t("colPerson")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((j) => (
                <tr key={j.id} data-testid="admin-overview-failed-row" className="border-b border-outline-variant/50 last:border-0">
                  <td className="whitespace-nowrap px-2 py-2.5">{when(j.failed_at)}</td>
                  <td className="whitespace-nowrap px-2 py-2.5">{t(failedKindKey(j.kind))}</td>
                  <td className="px-2 py-2.5">{reason(j.error_code)}</td>
                  <td className="px-2 py-2.5">{j.user_email ?? NONE}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="sm:hidden">
            {items.map((j) => (
              <div key={j.id} className="border-b border-outline-variant/50 py-2.5 text-[13px] last:border-0">
                <div className="flex justify-between gap-2">
                  <b className="font-semibold">{t(failedKindKey(j.kind))}</b>
                  <span className="text-[12px] text-on-surface-variant">{when(j.failed_at)}</span>
                </div>
                <div>{reason(j.error_code)}</div>
                <div className="text-[12px] text-on-surface-variant">{j.user_email ?? NONE}</div>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[12px] text-on-surface-variant">{t("failedHint")}</p>
        </>
      )}
    </AdminCard>
  );
}

function ConfigCard({ data, settings }: { data: AdminDashboardResponse; settings: InstanceSettingsResponse | null }) {
  const t = useTranslations("adminDashboard");
  const provider = settings ? findSetting(settings, SETTING_KEYS.provider) : undefined;
  const linkedin = settings ? findSetting(settings, SETTING_KEYS.linkedin) : undefined;
  const onOff = (v: boolean) => (v ? t("valueOn") : t("valueOff"));
  const row = (label: string, value: string, badge: React.ReactNode, testId: string) => (
    <div data-testid={testId} className="flex items-center justify-between gap-3 border-b border-outline-variant/50 py-2 text-[13px] last:border-0">
      <span>{label}</span>
      <span className="flex items-center gap-1.5 text-right font-semibold">
        {value}
        {badge}
      </span>
    </div>
  );
  return (
    <AdminCard testId="admin-overview-config" title={t("configTitle")}>
      {row(
        t("configProvider"),
        [data.health.llm_provider, data.health.llm_model].filter(Boolean).join(" · "),
        provider && <SourceBadge source={provider.source} />,
        "admin-overview-config-provider",
      )}
      {linkedin && row(t("configLinkedin"), onOff(linkedin.value === true), <SourceBadge source={linkedin.source} />, "admin-overview-config-linkedin")}
      {row(t("configRetention"), onOff(data.retention.enabled), <SourceBadge source={data.retention.source} />, "admin-overview-config-retention")}
      <div className="mt-3">
        <CardLink href="/admin/settings">{t("configLink")}</CardLink>
      </div>
    </AdminCard>
  );
}

function Activity() {
  const t = useTranslations("adminDashboard");
  const audit = useAdminResource(() => getAuditPage({ limit: 3 }), []);
  return (
    <AdminCard testId="admin-overview-activity" title={t("activityTitle")} action={<CardLink href="/admin/audit">{t("activityLink")}</CardLink>}>
      {audit.status === "ready" ? audit.data.items.map((e) => <AuditRowCompact key={e.id} event={e} />) : null}
    </AdminCard>
  );
}

export function AdminOverview() {
  const t = useTranslations("adminDashboard");
  const locale = useLocale();
  const nf = new Intl.NumberFormat(locale);
  const dash = useAdminDashboard();
  const usage = useAdminUsage(30);
  const settings = useInstanceSettings();

  if (dash.status === "forbidden") return <AdminForbidden />;
  if (dash.status === "error") return <LoadFailed testId="admin-overview-failed-load" onRetry={dash.reload} />;
  if (dash.status === "loading") return <Loading />;

  const d = dash.data;
  const others = Math.max(0, d.users.total - 1);
  const retentionOff = !d.retention.enabled && others > 0;
  const newestFailed = d.failed_jobs.items[0];

  return (
    <div data-testid="admin-overview" className="flex flex-col gap-4">
      <p className="text-[14px] text-on-surface-variant">{t("intro")}</p>
      <UpgradeNotice />
      {retentionOff && (
        <div data-testid="admin-overview-retention-off" className="rounded-lg border border-warning/40 bg-warning-container px-4 py-3 text-[13px] text-neutral-dark">
          <span className="mr-1">{t("retentionOffBanner", { count: others })}</span>
          <CardLink href="/admin/settings">{t("configLink")}</CardLink>
        </div>
      )}
      <HealthStrip health={d.health} />
      <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4 lg:gap-4">
        <Tile
          testId="admin-overview-tile-people"
          label={t("peopleTitle")}
          big={nf.format(d.users.total)}
          small={[t("peopleActive", { count: d.users.active }), t("peoplePending", { count: d.users.pending }), t("peopleDisabled", { count: d.users.disabled })].join(" · ")}
          link={<CardLink href="/admin/users">{t("peopleLink")}</CardLink>}
        />
        <Tile
          testId="admin-overview-tile-usage"
          label={t("usageTitle")}
          big={t("usageTokens", { tokens: nf.format(d.usage_30d.total_tokens) })}
          small={t("usageCalls", { calls: nf.format(d.usage_30d.calls), failed: nf.format(d.usage_30d.failed_calls) })}
          link={<CardLink href="/admin/usage">{t("usageLink")}</CardLink>}
        />
        <Tile
          testId="admin-overview-tile-failed"
          label={t("failedTitle")}
          big={t("failedCount", { count: d.failed_jobs.count })}
          tone={d.failed_jobs.count > 0 ? "warning" : undefined}
          small={newestFailed ? t(failedReason(newestFailed.error_code).key, failedReason(newestFailed.error_code).params) : undefined}
        />
        <Tile
          testId="admin-overview-tile-version"
          label={t("versionTitle")}
          big={d.health.version}
          small={t("versionProvider", { provider: [d.health.llm_provider, d.health.llm_model].filter(Boolean).join(" · ") })}
        />
      </div>
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[3fr_2fr]">
        <div className="flex min-w-0 flex-col gap-4">
          {usage.status === "ready" && <TopUsers usage={usage.data} />}
          <FailedJobs data={d} />
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <ConfigCard data={d} settings={settings.status === "ready" ? settings.data : null} />
          <Activity />
        </div>
      </div>
    </div>
  );
}
