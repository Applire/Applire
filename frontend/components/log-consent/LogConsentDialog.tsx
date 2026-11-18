"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * ADR-096 amended 2026-11-18 — any API call answered `409 log_consent_required`
 * (event raised by the global fetch patch) opens this dialog: the person has no
 * current consent to the test system's logs, so model work is refused.
 */

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";

import { ActionButton, Dialog } from "@/components/account/Dialog";
import { useCurrentUser } from "@/lib/auth";
import { LOG_CONSENT_REQUIRED_EVENT, longDate } from "@/lib/log-consent";

export function LogConsentDialog() {
  const t = useTranslations("logConsent");
  const locale = useLocale();
  const router = useRouter();
  const { user, refresh } = useCurrentUser();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onRequired = () => {
      setOpen(true);
      void refresh();
    };
    window.addEventListener(LOG_CONSENT_REQUIRED_EVENT, onRequired);
    return () => window.removeEventListener(LOG_CONSENT_REQUIRED_EVENT, onRequired);
  }, [refresh]);

  const close = () => setOpen(false);
  const consent = user?.log_consent;
  const body =
    consent?.state === "withdrawn" && consent.withdrawn_at
      ? t("cardWithdrawn", { date: longDate(consent.withdrawn_at, locale) })
      : t("cardOutdated");
  return (
    <Dialog
      open={open}
      onClose={close}
      title={t("cardTitle")}
      testId="log-consent-dialog"
      actions={
        <>
          <ActionButton data-testid="log-consent-dialog-close" onClick={close}>
            {t("close")}
          </ActionButton>
          <ActionButton
            tone="primary"
            data-testid="log-consent-dialog-agree"
            onClick={() => {
              close();
              router.push("/settings#log-consent");
            }}
          >
            {t("agreeAgain")}
          </ActionButton>
        </>
      }
    >
      <p>{body}</p>
    </Dialog>
  );
}
