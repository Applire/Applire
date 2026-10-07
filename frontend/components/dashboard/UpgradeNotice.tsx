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

/**
 * The operator's version-jump notice (US310 / #687, ADR-087).
 *
 * An upgrade used to change behaviour with no message on any surface the operator
 * reads. This is the third of the three surfaces the story gives it — the other two
 * are a WARNING block on the backend log at startup and `upgrade_notice` on
 * `GET /api/ops/health`. All three are computed once, in the lifespan, from the same
 * comparison; this component only renders what the ops report says.
 *
 * Strawberry (RD-1, S-16): the fields left the public `/health` for the
 * admin-or-probe `/api/ops/health`, so the notice is ADMIN-ONLY — a non-admin never
 * requests it. Crossing into the multi-user release (from < 0.43.0) adds the
 * W0-B upgrade mock's parts: the AUTH_PROVIDER=none paragraph (MD-2), the to-do
 * list, and — RD-9 — how many older duplicate profiles were set aside.
 *
 * Two independent things can put it on screen, and they behave differently on purpose:
 *
 *   - `upgrade_notice` is an EVENT — settings a release introduced that this
 *     environment does not set, and settings whose meaning changed that it does.
 *     It is dismissable: dismissing records the running version as seen.
 *   - `debug_log_on` is a live POSTURE — the LLM debug log writes every prompt,
 *     CV data included, to files inside the container, with no size or age cap by
 *     decision. It carries no dismiss control: the way to make it go away is to turn
 *     the log off. Dismissing the upgrade notice does not silence it.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { AlertTriangle, X } from "lucide-react";

import { API_BASE, useCurrentUser } from "@/lib/auth";

/** The release that introduced accounts (ADR-091) — the notice's to-do list is for crossing it. */
const MULTI_USER_RELEASE = [0, 43, 0] as const;

function releaseTuple(v: string | undefined): number[] | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec((v ?? "").trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

function before(a: readonly number[], b: readonly number[]): boolean {
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] < b[i];
  return false;
}

/** True when this upgrade went from a pre-accounts release to one with accounts. */
export function crossesMultiUser(from: string, to: string): boolean {
  const f = releaseTuple(from);
  const t = releaseTuple(to);
  return f !== null && t !== null && before(f, MULTI_USER_RELEASE) && !before(t, MULTI_USER_RELEASE);
}

interface NoticeItem {
  env_var: string;
  introduced_in?: string;
  semantics_changed_in?: string;
  default: string;
  description: string;
}

interface UpgradeNoticePayload {
  from: string;
  to: string;
  unset: NoticeItem[];
  re_meant: NoticeItem[];
}

interface OpsPayload {
  upgrade_notice?: UpgradeNoticePayload | null;
  debug_log_on?: boolean;
  /** RD-9 / MD-27: older duplicate profiles migration 0074 set aside (count only). */
  retired_profiles?: number;
}

export function UpgradeNotice() {
  const t = useTranslations("upgradeNotice");
  const { isAdmin } = useCurrentUser();
  const [notice, setNotice] = useState<UpgradeNoticePayload | null>(null);
  const [debugLogOn, setDebugLogOn] = useState(false);
  const [retired, setRetired] = useState(0);
  const [dismissing, setDismissing] = useState(false);

  useEffect(() => {
    if (!isAdmin) return;
    let stopped = false;
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/ops/health`, { credentials: "same-origin" });
        // 503 is the "down" verdict and still carries the report (contract §3.1);
        // 401/403 means this is not (or no longer) an admin session — nothing to show.
        if (!res.ok && res.status !== 503) return;
        const data = (await res.json()) as OpsPayload;
        if (stopped) return;
        setNotice(data.upgrade_notice ?? null);
        setDebugLogOn(Boolean(data.debug_log_on));
        setRetired(typeof data.retired_profiles === "number" ? data.retired_profiles : 0);
      } catch {
        // Non-fatal: a dashboard that cannot reach the ops report has bigger
        // problems than a missing notice, and they are already visible elsewhere.
      }
    }
    void load();
    return () => {
      stopped = true;
    };
  }, [isAdmin]);

  const dismiss = useCallback(async () => {
    setDismissing(true);
    try {
      await fetch(`${API_BASE}/api/settings/upgrade-notice/dismiss`, {
        method: "POST",
        credentials: "same-origin",
      });
      setNotice(null);
    } catch {
      // Leave the notice standing rather than pretending it was acknowledged.
      setDismissing(false);
    }
  }, []);

  if (!isAdmin || (!notice && !debugLogOn)) return null;

  const crossing = notice !== null && crossesMultiUser(notice.from, notice.to);
  const authReMeant = notice?.re_meant.some((item) => item.env_var === "AUTH_PROVIDER") ?? false;
  // The AUTH_PROVIDER paragraph replaces its generic re-meant line (no double mention).
  const reMeant = (notice?.re_meant ?? []).filter((item) => !(authReMeant && item.env_var === "AUTH_PROVIDER"));

  return (
    <div
      data-testid="upgrade-notice"
      className="rounded-xl border border-warning/40 bg-warning-container px-4 py-3.5 mb-4"
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangle
          className="h-4 w-4 shrink-0 mt-0.5 text-neutral-dark"
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          {notice && (
            <>
              <p className="text-[13px] font-bold text-neutral-dark">
                {t("title")}
              </p>
              <p className="text-[12px] text-on-surface-variant mt-0.5">
                {t("versions", { from: notice.from, to: notice.to })}
              </p>

              {authReMeant && (
                <p data-testid="upgrade-notice-auth" className="text-[12px] text-neutral-dark mt-2.5">
                  {t("authReMeant")}
                </p>
              )}

              {crossing && (
                <div data-testid="upgrade-notice-next-steps" className="mt-2.5">
                  <p className="text-[12px] font-bold text-neutral-dark">{t("nextStepsTitle")}</p>
                  <ul className="mt-1 list-disc pl-5 flex flex-col gap-0.5 text-[12px] text-neutral-dark">
                    <li>{t("nextStepAgent")}</li>
                    <li>{t("nextStepScripts")}</li>
                    <li>{t("nextStepMonitor")}</li>
                    <li>{t("nextStepPeople")}</li>
                  </ul>
                </div>
              )}

              {notice.unset.length > 0 && (
                <div className="mt-2.5">
                  <p className="text-[12px] font-bold text-neutral-dark">
                    {t("unsetHeading")}
                  </p>
                  <ul className="mt-1 flex flex-col gap-1">
                    {notice.unset.map((item) => (
                      <li key={item.env_var} className="text-[12px] text-neutral-dark">
                        <code className="font-mono font-bold">{item.env_var}</code>
                        <span className="text-on-surface-variant ml-1">
                          {t("introducedIn", {
                            version: item.introduced_in ?? "",
                            defaultValue: item.default || t("emptyDefault"),
                          })}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {reMeant.length > 0 && (
                <div className="mt-2.5">
                  <p className="text-[12px] font-bold text-neutral-dark">
                    {t("reMeantHeading")}
                  </p>
                  <ul className="mt-1 flex flex-col gap-1">
                    {reMeant.map((item) => (
                      <li key={item.env_var} className="text-[12px] text-neutral-dark">
                        <code className="font-mono font-bold">{item.env_var}</code>
                        <span className="text-on-surface-variant ml-1">
                          {t("changedIn", {
                            version: item.semantics_changed_in ?? "",
                          })}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {retired > 0 && (
                <p data-testid="upgrade-notice-retired" className="text-[12px] text-on-surface-variant mt-2.5">
                  {t("retiredProfile", { count: retired })}
                </p>
              )}

              <p className="text-[12px] text-on-surface-variant mt-2.5">
                {t("moreInfo")}
              </p>
            </>
          )}

          {debugLogOn && (
            <p
              data-testid="upgrade-notice-debug-log"
              className={`text-[12px] font-bold text-neutral-dark ${notice ? "mt-2.5" : ""}`}
            >
              {t("debugLogOn")}
            </p>
          )}
        </div>

        {notice && (
          <button
            type="button"
            onClick={() => void dismiss()}
            disabled={dismissing}
            aria-label={t("dismissAria")}
            data-testid="upgrade-notice-dismiss"
            className="shrink-0 rounded-lg p-1 text-on-surface-variant hover:bg-warning/20 disabled:opacity-50"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        )}
      </div>
    </div>
  );
}
