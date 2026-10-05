// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * US330 (ADR-091 cl. 14, S-14, MD-1, W0B-2) — /setup: claim the instance with
 * the per-boot setup code from the backend log. Mock: w0b/setup.html (screens
 * 1–5). On success the server signs the person in (204 + cookie, W0-3).
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { postJson, readErrorCode, readErrorMessage, useCurrentUser } from "@/lib/auth";

import {
  AuthAlert,
  AuthCard,
  AuthSubtitle,
  AuthTitle,
  CommandBlock,
  Field,
  OriginMismatchAlert,
  passwordProblems,
  usePasswordProblemText,
  type PasswordProblem,
} from "../_components/auth-ui";

const LOGS_COMMAND = "docker compose logs backend";
const CLI_COMMAND = "docker compose exec backend python -m applire.admin create-admin --email ";

type Phase = "form" | "done" | "harness";

export default function SetupPage() {
  const t = useTranslations("auth");
  const router = useRouter();
  const { status, authState } = useCurrentUser();
  const problemText = usePasswordProblemText();

  const [phase, setPhase] = useState<Phase>("form");
  const [code, setCode] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [codeError, setCodeError] = useState(false);
  const [problems, setProblems] = useState<PasswordProblem[]>([]);
  const [origin, setOrigin] = useState<{ message: string | null } | null>(null);
  const [network, setNetwork] = useState(false);
  const [working, setWorking] = useState(false);

  // The instance state decides which card shows before anyone types.
  useEffect(() => {
    if (!authState) return;
    if (authState.harness) setPhase("harness");
    else if (!authState.setup_required) setPhase("done");
  }, [authState]);

  // A signed-in person has nothing to set up.
  useEffect(() => {
    if (status === "authenticated" && authState && !authState.harness && !authState.setup_required) {
      router.replace("/");
    }
  }, [status, authState, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (working) return;
    setNetwork(false);
    setOrigin(null);
    setCodeError(false);
    const found = passwordProblems(password, repeat, email);
    setProblems(found);
    if (!code.trim() || !email.trim() || found.length) {
      if (!code.trim()) setCodeError(true);
      return;
    }
    setWorking(true);
    try {
      const res = await postJson("/api/setup", { setup_token: code.trim(), email: email.trim(), password });
      if (res.status === 204 || res.ok) {
        window.location.assign("/");
        return;
      }
      const errorCode = await readErrorCode(res);
      if (errorCode === "invalid_setup_token") setCodeError(true);
      else if (errorCode === "setup_done") setPhase("done");
      else if (errorCode === "harness_active") setPhase("harness");
      else if (errorCode === "origin_mismatch") setOrigin({ message: await readErrorMessage(res) });
      else if (res.status === 422) setProblems(passwordProblems(password, password, email).length ? passwordProblems(password, password, email) : ["tooShort"]);
      else setNetwork(true);
    } catch {
      setNetwork(true);
    }
    setWorking(false);
  }

  if (phase === "done") {
    return (
      <AuthCard testId="setup-done">
        <AuthTitle>{t("setupTitle")}</AuthTitle>
        <div className="mt-3">
          <AuthAlert tone="info" icon="check_circle">
            {t("errorSetupDone")}
          </AuthAlert>
        </div>
        <Button className="w-full" onClick={() => router.push("/login")}>
          {t("goToLogin")}
        </Button>
      </AuthCard>
    );
  }

  if (phase === "harness") {
    return (
      <AuthCard testId="setup-harness">
        <AuthTitle>{t("setupTitle")}</AuthTitle>
        <div className="mt-3">
          <AuthAlert tone="critical" icon="science">
            {t("errorHarnessActive")}
          </AuthAlert>
        </div>
      </AuthCard>
    );
  }

  const passwordError = problemText(problems);

  return (
    <AuthCard testId="setup-card">
      {origin && <OriginMismatchAlert serverMessage={origin.message} />}
      {network && (
        <AuthAlert tone="warning" icon="cloud_off" testId="auth-error">
          {t("errorNetwork")}
        </AuthAlert>
      )}
      <AuthTitle>{t("setupTitle")}</AuthTitle>
      <AuthSubtitle>{t("setupIntro")}</AuthSubtitle>
      <AuthAlert tone="info" icon="inventory_2" testId="setup-upgrade-note">
        {t("setupUpgradeNote")}
      </AuthAlert>

      <form onSubmit={submit} noValidate>
        <Field id="setup-code" label={t("setupCodeLabel")} error={codeError ? t("errorSetupCode") : null}>
          <Input
            id="setup-code"
            autoComplete="off"
            spellCheck={false}
            placeholder={t("setupCodePlaceholder")}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            error={codeError}
            className="font-mono tracking-wider"
            aria-invalid={codeError}
            aria-describedby={codeError ? "setup-code-error" : undefined}
          />
        </Field>

        <details
          className="-mt-1.5 mb-4 rounded-lg bg-surface-container px-3 py-2.5 text-[12.5px] leading-normal text-on-surface-variant"
          open={!codeError}
          data-testid="setup-code-where"
        >
          <summary className="cursor-pointer font-semibold text-teal">{t("setupCodeWhere")}</summary>
          <p className="mt-2">{t("setupCodeHint")}</p>
          <CommandBlock>{LOGS_COMMAND}</CommandBlock>
          <p>{t("setupCodeRotates")}</p>
          <p className="mt-2">{t("setupCliAlt")}</p>
          <CommandBlock>{CLI_COMMAND + t("emailPlaceholder")}</CommandBlock>
        </details>

        <Field id="setup-email" label={t("emailLabel")}>
          <Input
            id="setup-email"
            type="email"
            autoComplete="username"
            placeholder={t("emailPlaceholder")}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field id="setup-password" label={t("setupPasswordLabel")} hint={t("passwordPolicyHint")}>
          <Input
            id="setup-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={problems.some((p) => p !== "mismatch")}
          />
        </Field>
        <Field id="setup-repeat" label={t("passwordRepeatLabel")} error={passwordError}>
          <Input
            id="setup-repeat"
            type="password"
            autoComplete="new-password"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
            error={problems.length > 0}
            aria-describedby={passwordError ? "setup-repeat-error" : undefined}
          />
        </Field>
        <Button type="submit" className="mt-1 w-full" disabled={working} data-testid="setup-submit">
          {t("setupButton")}
        </Button>
      </form>
    </AuthCard>
  );
}
