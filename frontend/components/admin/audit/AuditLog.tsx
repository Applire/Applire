"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Administration → Protokoll (S-11; c2 mock admin-audit.html). Filter by
 * category (pills → the server's own action names), person (rows written BY
 * that person — "who did that"), and period; newest first; keyset "load older".
 * Contract §3: metadata only, no IP (RD-11), no secret values.
 */

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import { actionsForCategory, type AdminResult, type AuditCategory } from "@/lib/api/admin";
import { useAdminResource, useAuditLog } from "@/lib/api/admin-hooks";
import { listUsers, type AdminUser } from "../users/api";
import { AdminForbidden, LoadFailed, Loading, NONE } from "../common";
import { AuditRowCompact, useAuditText } from "./AuditRow";

const CATEGORIES: { key: AuditCategory; labelKey: string }[] = [
  { key: "all", labelKey: "filterAll" },
  { key: "accounts", labelKey: "filterAccounts" },
  { key: "access", labelKey: "filterAccess" },
  { key: "settings", labelKey: "filterSettings" },
  { key: "instance", labelKey: "filterInstance" },
];

/** `YYYY-MM-DD` (date input) → ISO start of that local day; `until` is exclusive, so +1 day. */
function dayStart(v: string, plusDays = 0): string | undefined {
  if (!v) return undefined;
  const d = new Date(`${v}T00:00:00`);
  if (Number.isNaN(d.getTime())) return undefined;
  d.setDate(d.getDate() + plusDays);
  return d.toISOString();
}

/** The person filter's options: the people list (admin route, metadata only). */
async function listUsersForFilter(): Promise<AdminResult<AdminUser[]>> {
  const users = await listUsers();
  return users ? { ok: true, data: users } : { ok: false, kind: "network", status: 0 };
}

export function AuditLog() {
  const t = useTranslations("adminAudit");
  const a = useAuditText();
  const [category, setCategory] = useState<AuditCategory>("all");
  const [actorId, setActorId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [knownActions, setKnownActions] = useState<string[]>([]);
  const people = useAdminResource(listUsersForFilter, []);

  const filter = useMemo(
    () => ({
      actions: actionsForCategory(knownActions, category),
      actorId: actorId || undefined,
      since: dayStart(from),
      until: dayStart(to, 1),
      limit: 50,
    }),
    [knownActions, category, actorId, from, to],
  );
  const log = useAuditLog(filter);
  if (log.actions.length > 0 && log.actions.join() !== knownActions.join()) {
    // Adjusting state while rendering (React docs): the action list arrives with the first page.
    setKnownActions(log.actions);
  }

  if (log.status === "forbidden") return <AdminForbidden />;

  const pill = (c: (typeof CATEGORIES)[number]) => (
    <button
      key={c.key}
      type="button"
      data-testid={`admin-audit-filter-${c.key}`}
      aria-pressed={category === c.key}
      onClick={() => setCategory(c.key)}
      className={`rounded-full border px-3 py-1 text-[12.5px] font-semibold ${
        category === c.key ? "border-outline-variant bg-primary-container text-primary" : "border-outline-variant bg-white text-on-surface-variant"
      }`}
    >
      {t(c.labelKey)}
    </button>
  );
  const field = "h-[38px] rounded-lg border border-outline-variant bg-white px-3 text-[13px] text-on-surface";

  return (
    <div data-testid="admin-audit" className="flex flex-col gap-4">
      <p className="text-[14px] text-on-surface-variant">{t("intro")}</p>
      <div className="flex flex-wrap items-end gap-2.5">
        <div className="flex w-full flex-wrap gap-1.5">{CATEGORIES.map(pill)}</div>
        <label className="flex flex-col gap-1 text-[12px] font-semibold text-on-surface-variant">
          {t("filterPerson")}
          <select data-testid="admin-audit-filter-person" value={actorId} onChange={(e) => setActorId(e.target.value)} className={`${field} min-w-[180px]`}>
            <option value="">{t("filterPersonAll")}</option>
            {people.status === "ready" &&
              people.data.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.email}
                </option>
              ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[12px] font-semibold text-on-surface-variant">
          {t("filterFrom")}
          <input data-testid="admin-audit-filter-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={field} />
        </label>
        <label className="flex flex-col gap-1 text-[12px] font-semibold text-on-surface-variant">
          {t("filterTo")}
          <input data-testid="admin-audit-filter-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} className={field} />
        </label>
      </div>

      {log.status === "error" ? (
        <LoadFailed testId="admin-audit-load-failed" />
      ) : log.status === "loading" ? (
        <Loading />
      ) : log.items.length === 0 ? (
        <div data-testid="admin-audit-empty" className="rounded-lg bg-white p-8 text-center text-[13px] text-on-surface-variant shadow-soft">
          {t("empty")}
        </div>
      ) : (
        <>
          <div className="hidden overflow-x-auto rounded-lg bg-white px-4 py-2 shadow-soft md:block">
            <table data-testid="admin-audit-table" className="w-full text-left text-[13px]">
              <thead>
                <tr className="border-b border-outline-variant text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">
                  <th className="px-2 py-2.5">{t("colWhen")}</th>
                  <th className="px-2 py-2.5">{t("colActor")}</th>
                  <th className="px-2 py-2.5">{t("colAction")}</th>
                  <th className="px-2 py-2.5">{t("colTarget")}</th>
                  <th className="px-2 py-2.5">{t("colDetail")}</th>
                </tr>
              </thead>
              <tbody>
                {log.items.map((e) => {
                  const target = a.target(e);
                  const detail = a.detail(e);
                  return (
                    <tr key={e.id} data-testid="admin-audit-row" data-action={e.action} className="border-b border-outline-variant/50 last:border-0">
                      <td className="whitespace-nowrap px-2 py-3">{a.when(e)}</td>
                      <td className="whitespace-nowrap px-2 py-3">{a.actor(e)}</td>
                      <td className="whitespace-nowrap px-2 py-3 font-semibold">{a.label(e)}</td>
                      <td className={`whitespace-nowrap px-2 py-3 ${e.target_user_id && !e.target_email ? "italic text-on-surface-variant" : ""}`}>{target ?? NONE}</td>
                      <td className="px-2 py-3 text-on-surface-variant">{detail || NONE}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="rounded-lg bg-white px-4 py-1 shadow-soft md:hidden">
            {log.items.map((e) => (
              <AuditRowCompact key={e.id} event={e} />
            ))}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span data-testid="admin-audit-count" className="text-[12px] text-on-surface-variant">
              {t("showing", { shown: log.items.length })}
            </span>
            {log.hasMore && (
              <button
                type="button"
                data-testid="admin-audit-load-more"
                disabled={log.loadingMore}
                onClick={() => void log.loadMore()}
                className="h-10 rounded-lg border border-outline-variant bg-white px-4 text-[13px] font-semibold text-on-surface disabled:opacity-60"
              >
                {t("loadMore")}
              </button>
            )}
          </div>
        </>
      )}
      <p className="text-[12px] text-on-surface-variant">{t("retentionNote")}</p>
    </div>
  );
}
