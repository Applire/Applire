// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * G-5 (Hazelnut build 4; ADR-059 amended 2026-10-24) — every denial the
 * candidate gave, with "Zurücknehmen". The withdrawal is the candidate's
 * explicit act (cl. 3): a confirm dialog quotes their own statement and names
 * what changes, then `POST /api/profile/denials/withdraw`. Withdrawn denials
 * stay listed, folded and read-only — the history is never erased (cl. 4).
 * Mock: Documents/Runs/Hazelnut/build-4/v/mock/v5-undenial-de.html (V-5).
 */
import { useState } from "react";
import { createPortal } from "react-dom";
import { useLocale, useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export interface DeniedConceptItem {
  concept: string;
  statement: string;
  source: "interview" | "agent_interview" | "testimony";
  date: string;
}

export interface WithdrawnDenialItem extends DeniedConceptItem {
  withdrawn_at: string;
}

function formatDay(iso: string, locale: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(locale === "de" ? "de-DE" : "en-GB");
}

export function DeniedConceptsCard({
  denied,
  withdrawn,
  onWithdrawn,
}: {
  denied: DeniedConceptItem[];
  withdrawn: WithdrawnDenialItem[];
  onWithdrawn: () => Promise<void> | void;
}) {
  const t = useTranslations("deniedConcepts");
  const locale = useLocale();
  const [pending, setPending] = useState<DeniedConceptItem | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  if (denied.length === 0 && withdrawn.length === 0) return null;

  const close = () => {
    if (busy) return;
    setPending(null);
    setFailed(false);
  };

  async function confirm() {
    if (!pending || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const res = await fetch(`${API_BASE}/api/profile/denials/withdraw`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ concept: pending.concept }),
      });
      // 409 = already withdrawn elsewhere: the list is stale, refresh it.
      if (!res.ok && res.status !== 409) throw new Error(String(res.status));
      setPending(null);
      await onWithdrawn();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card id="denials" data-testid="denied-concepts" className="p-4 mt-6 scroll-mt-24">
      <h3 className="font-heading text-base font-semibold text-neutral-dark">{t("title")}</h3>
      {denied.length > 0 ? (
        <p className="mt-1 mb-3 text-sm text-on-surface-variant">{t("lead")}</p>
      ) : (
        <p className="mt-1 mb-3 text-sm text-on-surface-variant">{t("none")}</p>
      )}
      <ul>
        {denied.map((d) => (
          <li
            key={d.concept}
            data-testid="denied-concept"
            className="flex items-start gap-3 border-t border-outline-variant py-2.5 first:border-t-0"
          >
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-on-surface break-words">{d.concept}</p>
              {d.statement && (
                <p className="mt-0.5 text-[13px] italic text-on-surface-variant break-words">
                  {t("quoted", { statement: d.statement })}
                </p>
              )}
              <p className="mt-0.5 text-xs text-on-surface-variant">
                {t(`source.${d.source}`, { date: formatDay(d.date, locale) })}
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              data-testid="denied-concept-withdraw"
              onClick={() => {
                setFailed(false);
                setPending(d);
              }}
            >
              {t("withdraw")}
            </Button>
          </li>
        ))}
      </ul>
      {withdrawn.length > 0 && (
        <details data-testid="withdrawn-denials" className="mt-2 border-t border-outline-variant pt-2">
          <summary className="cursor-pointer text-[13px] text-on-surface-variant">
            {t("withdrawnTitle", { count: withdrawn.length })}
          </summary>
          <ul className="mt-1">
            {withdrawn.map((w) => (
              <li key={`${w.concept}-${w.withdrawn_at}`} className="py-1 text-[13px] text-on-surface-variant">
                {t("withdrawnLine", { concept: w.concept, date: formatDay(w.withdrawn_at, locale) })}
              </li>
            ))}
          </ul>
        </details>
      )}

      {pending &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("dialogTitle", { concept: pending.concept })}
            data-testid="withdraw-denial-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
            onKeyDown={(e) => {
              if (e.key === "Escape") close();
            }}
          >
            <div className="w-full rounded-t-2xl bg-white p-6 shadow-xl md:max-w-md md:rounded-xl">
              <h3 className="mb-2 text-base font-bold text-on-surface">
                {t("dialogTitle", { concept: pending.concept })}
              </h3>
              {pending.statement ? (
                <p className="text-sm text-on-surface">
                  {t.rich("dialogSaid", {
                    date: formatDay(pending.date, locale),
                    statement: pending.statement,
                    concept: pending.concept,
                    i: (chunks) => <i>{chunks}</i>,
                  })}
                </p>
              ) : (
                <p className="text-sm text-on-surface">
                  {t("dialogSaidNoStatement", { concept: pending.concept })}
                </p>
              )}
              <p className="mt-2 text-sm text-on-surface">{t("dialogEffect", { concept: pending.concept })}</p>
              <p className="mt-2 text-xs text-on-surface-variant">{t("dialogHistory", { concept: pending.concept })}</p>
              {failed && (
                <p role="alert" className="mt-2 text-sm text-critical">
                  {t("failed")}
                </p>
              )}
              <div className="mt-4 flex justify-end gap-2">
                <Button size="sm" variant="outline" onClick={close} disabled={busy}>
                  {t("cancel")}
                </Button>
                <Button size="sm" data-testid="withdraw-denial-confirm" onClick={confirm} disabled={busy}>
                  {t("confirm")}
                </Button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </Card>
  );
}
