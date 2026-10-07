// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * US330 / S-7 — /forgot. Mock: w0b/forgot.html. With SMTP: one e-mail field,
 * then the same "if there is an account …" answer for every address (the API
 * always answers 202 — no account oracle). Without SMTP: who to ask, plus the
 * CLI reset for whoever runs the server (W0B-3: nobody is named).
 */

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { postJson, useCurrentUser } from "@/lib/auth";

import { AuthAlert, AuthCard, AuthSubtitle, AuthTitle, CommandBlock, Field } from "../_components/auth-ui";

const RESET_COMMAND = "docker compose exec backend python -m applire.admin reset-password --email ";

export default function ForgotPage() {
  const t = useTranslations("auth");
  const router = useRouter();
  const { authState } = useCurrentUser();
  const [email, setEmail] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [network, setNetwork] = useState(false);
  const [working, setWorking] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const value = email.trim();
    if (!value || working) return;
    setWorking(true);
    setNetwork(false);
    try {
      const res = await postJson("/api/auth/forgot", { email: value });
      // 202 always; a 422 (not an address) gets the same answer — no oracle either way.
      if (res.status >= 500) setNetwork(true);
      else setSentTo(value);
    } catch {
      setNetwork(true);
    }
    setWorking(false);
  }

  const backButton = (
    <Button variant="outline" className="w-full" onClick={() => router.push("/login")} data-testid="forgot-back">
      {t("backToLogin")}
    </Button>
  );

  if (!authState) {
    return <AuthCard testId="forgot-loading"><AuthTitle>{t("forgotTitle")}</AuthTitle></AuthCard>;
  }

  if (!authState.smtp_enabled) {
    return (
      <AuthCard testId="forgot-nomail">
        <AuthTitle>{t("forgotTitle")}</AuthTitle>
        <AuthSubtitle>{t("forgotNoMailIntro")}</AuthSubtitle>
        <div className="mb-4 rounded-lg bg-surface-container px-3 py-2.5 text-[12.5px] leading-normal text-on-surface-variant">
          <p>{t("forgotNoMailOperator")}</p>
          <CommandBlock>{RESET_COMMAND + t("emailPlaceholder")}</CommandBlock>
        </div>
        {backButton}
      </AuthCard>
    );
  }

  if (sentTo) {
    return (
      <AuthCard testId="forgot-sent">
        <AuthTitle>{t("forgotTitle")}</AuthTitle>
        <div className="mt-3">
          <AuthAlert tone="success" icon="mark_email_read">
            {t("forgotSent", { email: sentTo })}
          </AuthAlert>
        </div>
        {backButton}
      </AuthCard>
    );
  }

  return (
    <AuthCard testId="forgot-card">
      {network && (
        <AuthAlert tone="warning" icon="cloud_off" testId="auth-error">
          {t("errorNetwork")}
        </AuthAlert>
      )}
      <AuthTitle>{t("forgotTitle")}</AuthTitle>
      <AuthSubtitle>{t("forgotIntro")}</AuthSubtitle>
      <form onSubmit={submit} noValidate>
        <Field id="forgot-email" label={t("emailLabel")}>
          <Input
            id="forgot-email"
            type="email"
            autoComplete="username"
            placeholder={t("emailPlaceholder")}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Button type="submit" className="mt-1 w-full" disabled={working} data-testid="forgot-submit">
          {t("forgotButton")}
        </Button>
      </form>
      <p className="mt-4 text-center">
        <Link href="/login" className="text-[13px] font-medium text-teal hover:underline">
          {t("backToLogin")}
        </Link>
      </p>
    </AuthCard>
  );
}
