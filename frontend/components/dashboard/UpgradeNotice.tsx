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
 * `GET /health`. All three are computed once, in the lifespan, from the same
 * comparison; this component only renders what `/health` reports.
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

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

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

interface HealthPayload {
  upgrade_notice?: UpgradeNoticePayload | null;
  debug_log_on?: boolean;
}

export function UpgradeNotice() {
  const t = useTranslations("upgradeNotice");
  const [notice, setNotice] = useState<UpgradeNoticePayload | null>(null);
  const [debugLogOn, setDebugLogOn] = useState(false);
  const [dismissing, setDismissing] = useState(false);

  useEffect(() => {
    let stopped = false;
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/health`);
        if (!res.ok) return;
        const data = (await res.json()) as HealthPayload;
        if (stopped) return;
        setNotice(data.upgrade_notice ?? null);
        setDebugLogOn(Boolean(data.debug_log_on));
      } catch {
        // Non-fatal: a dashboard that cannot reach /health has bigger problems
        // than a missing notice, and they are already visible elsewhere.
      }
    }
    void load();
    return () => {
      stopped = true;
    };
  }, []);

  const dismiss = useCallback(async () => {
    setDismissing(true);
    try {
      await fetch(`${API_BASE}/api/settings/upgrade-notice/dismiss`, {
        method: "POST",
      });
      setNotice(null);
    } catch {
      // Leave the notice standing rather than pretending it was acknowledged.
      setDismissing(false);
    }
  }, []);

  if (!notice && !debugLogOn) return null;

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

              {notice.re_meant.length > 0 && (
                <div className="mt-2.5">
                  <p className="text-[12px] font-bold text-neutral-dark">
                    {t("reMeantHeading")}
                  </p>
                  <ul className="mt-1 flex flex-col gap-1">
                    {notice.re_meant.map((item) => (
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
