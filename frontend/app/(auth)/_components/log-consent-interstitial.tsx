"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Board B2 (ADR-091 amended 2026-11-18) — after sign-in, while the instance
 * writes content logs (LOG_LEVEL=DEBUG), a person without a current consent
 * (never asked, or the text changed) answers here before the shell renders.
 * Agreeing stores the consent for today's text; declining signs out and leaves
 * the account and profile untouched.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { ConsentCheckbox, ConsentTextDialog, ConsentTextLink } from "@/components/log-consent/ConsentText";
import { loginPathFor, safeNextPath, useCurrentUser } from "@/lib/auth";
import { agreeLogConsent, consentParams, needsConsent } from "@/lib/log-consent";

import { AuthAlert, AuthCard, AuthTitle } from "./auth-ui";

function nextPath(): string {
  return safeNextPath(new URLSearchParams(window.location.search).get("next"));
}

export function LogConsentInterstitial() {
  const t = useTranslations("logConsent");
  const router = useRouter();
  const { status, user, refresh, signOut } = useCurrentUser();
  const [checked, setChecked] = useState(false);
  const [textOpen, setTextOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<"retry" | "stale" | null>(null);

  const consent = user?.log_consent;

  useEffect(() => {
    if (status === "unauthenticated") router.replace(loginPathFor({ pathname: "/", search: "" }));
    else if (status === "authenticated" && !needsConsent(consent)) router.replace(nextPath());
  }, [status, consent, router]);

  if (status !== "authenticated" || !needsConsent(consent) || !consent?.text) {
    return <div className="h-40" aria-busy="true" data-testid="log-consent-pending" />;
  }
  const text = consent.text;
  const p = consentParams(text, t("notSet"));

  async function agree() {
    if (!checked || working) return;
    setWorking(true);
    setError(null);
    const result = await agreeLogConsent(text.version);
    if (result.ok) {
      await refresh();
      router.replace(nextPath());
      return;
    }
    setError(result.stale ? "stale" : "retry");
    if (result.stale) {
      setChecked(false);
      await refresh();
    }
    setWorking(false);
  }

  return (
    <AuthCard testId="log-consent-interstitial">
      {error && (
        <AuthAlert tone="warning" icon="error" testId="log-consent-error">
          {error === "stale" ? t("errorStale") : t("errorRetry")}
        </AuthAlert>
      )}
      <AuthTitle>{t("interTitle")}</AuthTitle>
      <p className="mb-1 text-sm leading-relaxed text-on-surface-variant">
        {t("interBody", p)} <ConsentTextLink onClick={() => setTextOpen(true)} />
      </p>
      <ConsentCheckbox checked={checked} onChange={setChecked} text={text} />
      <Button className="w-full" disabled={!checked || working} onClick={agree} data-testid="log-consent-agree">
        {t("agreeContinue")}
      </Button>
      <p className="mt-3 text-center text-sm">
        <button
          type="button"
          onClick={() => void signOut()}
          data-testid="log-consent-decline"
          className="font-semibold text-teal underline underline-offset-2 hover:text-teal/80"
        >
          {t("declineSignOut")}
        </button>
      </p>
      <ConsentTextDialog open={textOpen} onClose={() => setTextOpen(false)} text={text} />
    </AuthCard>
  );
}
