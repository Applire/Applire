"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Settings → "Protokolle in der Testphase" (board B3; ADR-091 amended
 * 2026-11-18). Rendered only while the instance writes content logs
 * (`me.log_consent.required`). Read the text, withdraw (one dialog), agree
 * again. After a withdrawal the account stays; only new model work is refused.
 */

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { ConsentTextDialog } from "@/components/log-consent/ConsentText";
import { useCurrentUser } from "@/lib/auth";
import {
  agreeLogConsent,
  consentParams,
  longDate,
  withdrawLogConsent,
  type LogConsentState,
} from "@/lib/log-consent";
import { ActionButton, Dialog, DialogError } from "./Dialog";

export function LogConsentCard() {
  const { user } = useCurrentUser();
  const consent = user?.log_consent;
  if (!consent?.required || !consent.text) return null;
  return <LogConsentCardBody consent={consent} />;
}

function LogConsentCardBody({ consent }: { consent: LogConsentState }) {
  const t = useTranslations("logConsent");
  const locale = useLocale();
  const { refresh } = useCurrentUser();
  const [textOpen, setTextOpen] = useState(false);
  const [withdrawOpen, setWithdrawOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<"retry" | "stale" | null>(null);

  // `/settings#log-consent` (from the refusal dialog) scrolls the card into view.
  useEffect(() => {
    if (window.location.hash === "#log-consent") {
      document.getElementById("log-consent")?.scrollIntoView({ block: "start" });
    }
  }, []);

  const text = consent.text!;
  const p = consentParams(text, t("notSet"));

  async function agree() {
    setWorking(true);
    setError(null);
    const result = await agreeLogConsent(text.version);
    if (result.ok) await refresh();
    else setError(result.stale ? "stale" : "retry");
    if (!result.ok && result.stale) await refresh();
    setWorking(false);
  }

  async function withdraw() {
    setWorking(true);
    setError(null);
    const result = await withdrawLogConsent();
    if (result.ok) {
      setWithdrawOpen(false);
      await refresh();
    } else setError("retry");
    setWorking(false);
  }

  let sentence: string;
  if (consent.state === "current" && consent.consented_at) {
    sentence = t("cardAgreed", { ...p, date: longDate(consent.consented_at, locale) });
  } else if (consent.state === "withdrawn" && consent.withdrawn_at) {
    sentence = t("cardWithdrawn", { date: longDate(consent.withdrawn_at, locale) });
  } else {
    sentence = t("cardOutdated");
  }

  return (
    <section
      id="log-consent"
      data-testid="log-consent-card"
      data-state={consent.state}
      className="scroll-mt-4 rounded-xl bg-white p-6 shadow-soft"
    >
      <h2 className="font-heading text-xl font-bold text-on-surface">{t("cardTitle")}</h2>
      <p className="mt-2 text-[14px] text-on-surface-variant" data-testid="log-consent-card-state">
        {sentence}
      </p>
      {error && !withdrawOpen && (
        <DialogError testId="log-consent-card-error">{error === "stale" ? t("errorStale") : t("errorRetry")}</DialogError>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <ActionButton data-testid="log-consent-read" onClick={() => setTextOpen(true)}>
          {t("readText")}
        </ActionButton>
        {consent.state === "current" ? (
          <ActionButton data-testid="log-consent-withdraw" onClick={() => setWithdrawOpen(true)}>
            {t("withdraw")}
          </ActionButton>
        ) : (
          <ActionButton tone="primary" data-testid="log-consent-agree" disabled={working} onClick={agree}>
            {t("agreeAgain")}
          </ActionButton>
        )}
      </div>

      <ConsentTextDialog open={textOpen} onClose={() => setTextOpen(false)} text={text} />
      <Dialog
        open={withdrawOpen}
        onClose={() => setWithdrawOpen(false)}
        title={t("withdrawTitle")}
        testId="log-consent-withdraw-dialog"
        actions={
          <>
            <ActionButton data-testid="log-consent-withdraw-cancel" onClick={() => setWithdrawOpen(false)}>
              {t("cancel")}
            </ActionButton>
            <ActionButton
              tone="danger"
              data-testid="log-consent-withdraw-confirm"
              disabled={working}
              onClick={withdraw}
            >
              {t("confirmWithdraw")}
            </ActionButton>
          </>
        }
      >
        <p>{t("withdrawDelete")}</p>
        <p className="mt-2">{t("withdrawEffect")}</p>
        {error && <DialogError testId="log-consent-withdraw-error">{t("errorRetry")}</DialogError>}
      </Dialog>
    </section>
  );
}
