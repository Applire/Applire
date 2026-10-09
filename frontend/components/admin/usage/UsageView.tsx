"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Administration → Nutzung (B2-2; c2 mock admin-usage.html). Per person: calls,
 * input/output tokens, share, failures, last call — over 7/30/90 days, from
 * `llm_usage.user_id` (S-8). Tokens, never currency. "By provider and model"
 * makes a #710 switch visible; "not attributed" = user_id NULL.
 */

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { formatWhen } from "@/components/account/format";
import { tokenShare, USAGE_PERIODS, type AdminUsageResponse, type UsagePeriod, type UsageTotals } from "@/lib/api/admin";
import { useAdminUsage } from "@/lib/api/admin-hooks";
import { AdminCard, AdminForbidden, LoadFailed, Loading, NONE } from "../common";

function Bar({ pct, grey, testId }: { pct: number; grey?: boolean; testId?: string }) {
  return (
    <span data-testid={testId} data-pct={pct} className="block h-1.5 w-full min-w-[60px] overflow-hidden rounded-full bg-surface-container">
      <span className={`block h-full ${grey ? "bg-on-surface-variant/40" : "bg-teal"}`} style={{ width: `${Math.max(pct > 0 ? 2 : 0, pct)}%` }} />
    </span>
  );
}

function Kpi({ label, value, testId }: { label: string; value: string; testId: string }) {
  return (
    <div data-testid={testId} className="flex min-w-0 flex-col gap-1.5 rounded-lg bg-white p-4 shadow-soft">
      <span className="text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">{label}</span>
      <span className="font-manrope text-2xl font-extrabold text-on-surface">{value}</span>
    </div>
  );
}

function Body({ data }: { data: AdminUsageResponse }) {
  const t = useTranslations("adminUsage");
  const tDash = useTranslations("adminDashboard");
  const ta = useTranslations("account");
  const locale = useLocale();
  const nf = new Intl.NumberFormat(locale);
  const total = data.totals.total_tokens;
  const when = (iso: string | null) => (iso ? formatWhen(iso, locale, ta) : NONE);
  const un: UsageTotals = data.unattributed;
  const anyCalls = data.totals.calls > 0;

  if (!anyCalls) {
    return (
      <div data-testid="admin-usage-empty" className="rounded-lg bg-white p-8 text-center text-[13px] text-on-surface-variant shadow-soft">
        {t("empty")}
      </div>
    );
  }

  const kinds = data.by_document_kind;
  return (
    <>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 sm:gap-4">
        <Kpi testId="admin-usage-total-tokens" label={t("totalTokens")} value={nf.format(total)} />
        <Kpi testId="admin-usage-total-calls" label={t("totalCalls")} value={nf.format(data.totals.calls)} />
        <Kpi testId="admin-usage-total-failed" label={t("totalFailed")} value={nf.format(data.totals.failed_calls)} />
      </div>

      <div className="hidden overflow-x-auto rounded-lg bg-white px-4 py-2 shadow-soft md:block">
        <table data-testid="admin-usage-table" className="w-full text-left text-[13px]">
          <thead>
            <tr className="border-b border-outline-variant text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">
              <th className="px-2 py-2.5">{t("colPerson")}</th>
              <th className="px-2 py-2.5 text-right">{t("colCalls")}</th>
              <th className="px-2 py-2.5 text-right">{t("colInput")}</th>
              <th className="px-2 py-2.5 text-right">{t("colOutput")}</th>
              <th className="px-2 py-2.5 text-right">{t("colTotal")}</th>
              <th className="w-[130px] px-2 py-2.5">{t("colShare")}</th>
              <th className="px-2 py-2.5 text-right">{t("colFailed")}</th>
              <th className="px-2 py-2.5">{t("colLast")}</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {data.users.map((u) => (
              <tr key={u.user_id} data-testid="admin-usage-row" data-email={u.email} className="border-b border-outline-variant/50">
                <td className="px-2 py-3">{u.email}</td>
                <td className="px-2 py-3 text-right">{nf.format(u.totals.calls)}</td>
                <td className="px-2 py-3 text-right">{nf.format(u.totals.prompt_tokens)}</td>
                <td className="px-2 py-3 text-right">{nf.format(u.totals.completion_tokens)}</td>
                <td className="px-2 py-3 text-right font-bold">{nf.format(u.totals.total_tokens)}</td>
                <td className="px-2 py-3">
                  <Bar pct={tokenShare(u.totals.total_tokens, total)} />
                </td>
                <td className="px-2 py-3 text-right">{nf.format(u.totals.failed_calls)}</td>
                <td className="whitespace-nowrap px-2 py-3">{when(u.last_call_at)}</td>
              </tr>
            ))}
            {un.calls > 0 && (
              <tr data-testid="admin-usage-unattributed" className="text-on-surface-variant">
                <td className="px-2 py-3">{tDash("unattributed")}</td>
                <td className="px-2 py-3 text-right">{nf.format(un.calls)}</td>
                <td className="px-2 py-3 text-right">{nf.format(un.prompt_tokens)}</td>
                <td className="px-2 py-3 text-right">{nf.format(un.completion_tokens)}</td>
                <td className="px-2 py-3 text-right font-bold">{nf.format(un.total_tokens)}</td>
                <td className="px-2 py-3">
                  <Bar pct={tokenShare(un.total_tokens, total)} grey />
                </td>
                <td className="px-2 py-3 text-right">{nf.format(un.failed_calls)}</td>
                <td className="px-2 py-3">{NONE}</td>
              </tr>
            )}
          </tbody>
        </table>
        {data.totals.estimated_calls > 0 && (
          <p data-testid="admin-usage-estimated" className="py-2 text-[12px] text-on-surface-variant">
            {[t("estimated", { count: data.totals.estimated_calls }), t("estimatedHint")].join(" — ")}
          </p>
        )}
      </div>

      <div className="rounded-lg bg-white px-4 py-1 shadow-soft md:hidden">
        {data.users.map((u) => (
          <div key={u.user_id} data-testid="admin-usage-card" className="border-b border-outline-variant/50 py-2.5 text-[13px] last:border-0">
            <div className="flex justify-between gap-2">
              <b className="min-w-0 truncate font-semibold">{u.email}</b>
              <span className="tabular-nums">{nf.format(u.totals.total_tokens)}</span>
            </div>
            <div className="my-1.5">
              <Bar pct={tokenShare(u.totals.total_tokens, total)} />
            </div>
            <div className="text-[12px] text-on-surface-variant">
              {[
                `${nf.format(u.totals.calls)} ${t("colCalls")}`,
                `${nf.format(u.totals.failed_calls)} ${t("colFailed")}`,
                `${t("colLast")}: ${when(u.last_call_at)}`,
              ].join(" · ")}
            </div>
          </div>
        ))}
        {un.calls > 0 && (
          <div className="flex justify-between py-2.5 text-[13px] text-on-surface-variant">
            <span>{tDash("unattributed")}</span>
            <span className="tabular-nums">{nf.format(un.total_tokens)}</span>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {kinds && (
          <AdminCard testId="admin-usage-by-kind" title={t("byKindTitle")}>
            {(
              [
                ["kindCv", kinds.cv],
                ["kindLetter", kinds.cover_letter],
                ["kindOther", kinds.other],
              ] as const
            ).map(([k, v]) => (
              <div key={k} className="flex items-center gap-3 border-b border-outline-variant/50 py-2 text-[13px] last:border-0">
                <span className="min-w-0 flex-1">{t(k)}</span>
                <span className="w-[70px] sm:w-[120px]">
                  <Bar pct={tokenShare(v.total_tokens, total)} />
                </span>
                <span className="tabular-nums text-on-surface-variant">{nf.format(v.total_tokens)}</span>
              </div>
            ))}
          </AdminCard>
        )}
        {data.by_provider.length > 0 && (
          <AdminCard testId="admin-usage-by-provider" title={t("byModelTitle")}>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[13px]">
                <thead>
                  <tr className="border-b border-outline-variant text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">
                    <th className="py-2 pr-2">{t("colProvider")}</th>
                    <th className="py-2 pr-2">{t("colModel")}</th>
                    <th className="py-2 pr-2 text-right">{t("colCalls")}</th>
                    <th className="py-2 text-right">{t("colTotal")}</th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {data.by_provider.map((p) => (
                    <tr key={`${p.provider}/${p.model}`} data-testid="admin-usage-provider-row" className="border-b border-outline-variant/50 last:border-0">
                      <td className="py-2.5 pr-2">{p.provider}</td>
                      <td className="break-all py-2.5 pr-2 font-mono text-[12px]">{p.model}</td>
                      <td className="py-2.5 pr-2 text-right">{nf.format(p.totals.calls)}</td>
                      <td className="py-2.5 text-right">{nf.format(p.totals.total_tokens)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </AdminCard>
        )}
      </div>
      {un.calls > 0 && <p className="text-[12px] text-on-surface-variant">{t("unattributedNote")}</p>}
    </>
  );
}

export function UsageView() {
  const t = useTranslations("adminUsage");
  const [days, setDays] = useState<UsagePeriod>(30);
  const usage = useAdminUsage(days);
  if (usage.status === "forbidden") return <AdminForbidden />;
  return (
    <div data-testid="admin-usage" className="flex flex-col gap-4">
      <p className="text-[14px] text-on-surface-variant">{t("intro")}</p>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t("period")}>
        <span className="text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">{t("period")}</span>
        {USAGE_PERIODS.map((d) => (
          <button
            key={d}
            type="button"
            data-testid={`admin-usage-period-${d}`}
            aria-pressed={days === d}
            onClick={() => setDays(d)}
            className={`rounded-full border px-3 py-1 text-[12.5px] font-semibold ${
              days === d ? "border-outline-variant bg-primary-container text-primary" : "border-outline-variant bg-white text-on-surface-variant"
            }`}
          >
            {t(`period${d}`)}
          </button>
        ))}
      </div>
      {usage.status === "error" ? (
        <LoadFailed testId="admin-usage-load-failed" onRetry={usage.reload} />
      ) : usage.status === "loading" ? (
        <Loading />
      ) : (
        <Body data={usage.data} />
      )}
    </div>
  );
}
