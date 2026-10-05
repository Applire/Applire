// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * US330 (ADR-091) — /login. Mocks: w0b/login.html (screens 1–8),
 * redirect-401.html (expired session; the share screen was dropped — ruling w4-2a-1), user-menu.html
 * screen 4 (signed out). Strings: `auth.*` (COPY.md, founder gate G-1).
 *
 * Query parameters it reads:
 *   next=<relative path>   where to go after sign-in (open-redirect guarded)
 *   expired=1              the global 401 handler sent us — session ended
 *   signed_out=1           a deliberate sign-out
 *   error=oidc_failed|oidc_no_account|account_disabled   OIDC callback failures
 *   oidc=failed            alias of error=oidc_failed (INTEGRATION-CHECKLIST)
 */

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  API_BASE,
  postJson,
  readErrorCode,
  readErrorMessage,
  safeNextPath,
  useCurrentUser,
} from "@/lib/auth";

import {
  AuthAlert,
  AuthCard,
  AuthSubtitle,
  AuthTitle,
  ButtonIcon,
  Divider,
  Field,
  OriginMismatchAlert,
} from "../_components/auth-ui";

type LoginError =
  | { kind: "invalid" }
  | { kind: "throttled" }
  | { kind: "disabled" }
  | { kind: "origin"; message: string | null }
  | { kind: "network" };

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginInner />
    </Suspense>
  );
}

function LoginInner() {
  const t = useTranslations("auth");
  const router = useRouter();
  const params = useSearchParams();
  const { status, authState } = useCurrentUser();

  const next = safeNextPath(params.get("next"));
  const expired = params.get("expired") === "1";
  const signedOut = params.get("signed_out") === "1";
  const oidcError = params.get("error") ?? (params.get("oidc") === "failed" ? "oidc_failed" : null);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<LoginError | null>(null);

  // Already signed in (e.g. the back button) → straight on. First run → setup.
  useEffect(() => {
    if (status === "authenticated") router.replace(next);
  }, [status, next, router]);
  useEffect(() => {
    if (authState?.setup_required) router.replace("/setup");
  }, [authState?.setup_required, router]);

  const oidcLabel = authState?.oidc_button_label ?? "";
  const oidcOn = !!authState?.oidc_enabled;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (working || !email.trim() || !password) return;
    setWorking(true);
    setError(null);
    try {
      const res = await postJson("/api/auth/login", { email: email.trim(), password });
      if (res.status === 204 || res.ok) {
        // Full navigation: every provider re-reads /api/auth/me with the new cookie.
        window.location.assign(next);
        return;
      }
      const code = await readErrorCode(res);
      if (res.status === 401) {
        setError(res.headers.get("X-Applire-Throttled") === "1" ? { kind: "throttled" } : { kind: "invalid" });
      } else if (code === "account_disabled") {
        setError({ kind: "disabled" });
      } else if (code === "origin_mismatch") {
        setError({ kind: "origin", message: await readErrorMessage(res) });
      } else if (res.status === 422) {
        setError({ kind: "invalid" });
      } else {
        setError({ kind: "network" });
      }
    } catch {
      setError({ kind: "network" });
    }
    setWorking(false);
  }

  function startOidc() {
    window.location.assign(`${API_BASE}/api/auth/oidc/start?next=${encodeURIComponent(next)}`);
  }

  // The expired-session notice replaces the subtitle and the "no account yet"
  // line (redirect-401 mock); signed-out keeps the subtitle and drops the links
  // (user-menu mock screen 4). A share deep link in ?next= shows NO notice —
  // the link survives sign-in and is prefilled (ruling w4-2a-1).
  const contextBanner = expired;

  return (
    <AuthCard testId="login-card">
      {error?.kind === "invalid" && (
        <AuthAlert tone="critical" icon="error" testId="auth-error">
          {t("errorInvalidCredentials")}
        </AuthAlert>
      )}
      {error?.kind === "throttled" && (
        <AuthAlert tone="warning" icon="hourglass_top" testId="auth-error">
          {t("errorThrottled")}
        </AuthAlert>
      )}
      {error?.kind === "disabled" && (
        <AuthAlert tone="critical" icon="block" testId="auth-error">
          {t("errorAccountDisabled")}
        </AuthAlert>
      )}
      {error?.kind === "origin" && <OriginMismatchAlert serverMessage={error.message} />}
      {error?.kind === "network" && (
        <AuthAlert tone="warning" icon="cloud_off" testId="auth-error">
          {t("errorNetwork")}
        </AuthAlert>
      )}
      {!error && oidcError === "oidc_failed" && (
        <AuthAlert tone="critical" icon="error" testId="auth-error">
          {t("errorOidcFailed", { label: oidcLabel })}
        </AuthAlert>
      )}
      {!error && oidcError === "oidc_no_account" && (
        <AuthAlert tone="info" icon="info" testId="auth-error">
          {t("errorOidcNoAccount")}
        </AuthAlert>
      )}
      {!error && oidcError === "account_disabled" && (
        <AuthAlert tone="critical" icon="block" testId="auth-error">
          {t("errorAccountDisabled")}
        </AuthAlert>
      )}
      {!error && expired && (
        <AuthAlert tone="info" icon="schedule" testId="auth-notice-expired">
          {t("sessionExpired")}
        </AuthAlert>
      )}
      {!error && signedOut && !contextBanner && (
        <AuthAlert tone="success" icon="logout" testId="auth-notice-signed-out">
          {t("signedOut")}
        </AuthAlert>
      )}

      <AuthTitle>{t("loginTitle")}</AuthTitle>
      {!contextBanner && <AuthSubtitle>{t("loginSubtitle")}</AuthSubtitle>}

      {oidcOn && (
        <>
          <Button
            type="button"
            variant="outline"
            className="h-11 w-full"
            onClick={startOidc}
            data-testid="login-oidc"
          >
            <ButtonIcon name="key" />
            {t("oidcButton", { label: oidcLabel })}
          </Button>
          <Divider>{t("orDivider")}</Divider>
        </>
      )}

      <form onSubmit={submit} noValidate>
        <Field id="login-email" label={t("emailLabel")}>
          <Input
            id="login-email"
            type="email"
            autoComplete="username"
            placeholder={t("emailPlaceholder")}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </Field>
        <Field id="login-password" label={t("passwordLabel")}>
          <Input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={error?.kind === "invalid"}
            required
          />
        </Field>
        <Button
          type="submit"
          className="mt-1 w-full disabled:bg-teal disabled:text-white disabled:opacity-60"
          disabled={working}
          data-testid="login-submit"
        >
          {working ? t("loginWorking") : t("loginButton")}
        </Button>
      </form>

      {!signedOut && (
        <p className="mt-4 text-center">
          <Link href="/forgot" className="text-[13px] font-medium text-teal hover:underline">
            {t("forgotLink")}
          </Link>
        </p>
      )}
      {!contextBanner && !signedOut && (
        <p className="mt-5 text-center text-[12.5px] leading-normal text-on-surface-variant">{t("noAccountHint")}</p>
      )}
    </AuthCard>
  );
}
