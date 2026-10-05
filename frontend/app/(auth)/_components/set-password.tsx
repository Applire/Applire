// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * US330 / ADR-091 cl. 22 — /invite#<token> and /reset#<token>. Mock:
 * w0b/invite-reset.html (screens 1–9). The token lives ONLY in the fragment
 * and in POST bodies (contract §1), never in a path or query.
 *
 * inspect → checking | form | expired | used | invalid
 * redeem  → 204 signs the person in (W0-3) → "/"
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { API_BASE, postJson, readErrorCode, readErrorMessage, useCurrentUser, type LinkInspect } from "@/lib/auth";

import {
  AuthAlert,
  AuthCard,
  AuthSubtitle,
  AuthTitle,
  ButtonIcon,
  Divider,
  Field,
  OriginMismatchAlert,
  StatusBlock,
  passwordProblems,
  usePasswordProblemText,
  type PasswordProblem,
} from "./auth-ui";

type View =
  | { kind: "checking" }
  | { kind: "form"; link: LinkInspect }
  | { kind: "expired"; purpose: "invite" | "reset" }
  | { kind: "used" }
  | { kind: "invalid" };

/** The token from `location.hash` (`#<token>`), or "". */
export function tokenFromHash(hash: string): string {
  return decodeURIComponent((hash ?? "").replace(/^#/, "").trim());
}

export function SetPasswordPage({ purpose }: { purpose: "invite" | "reset" }) {
  const t = useTranslations("auth");
  const router = useRouter();
  const { authState } = useCurrentUser();
  const problemText = usePasswordProblemText();

  const [token, setToken] = useState<string>("");
  const [view, setView] = useState<View>({ kind: "checking" });
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [problems, setProblems] = useState<PasswordProblem[]>([]);
  const [origin, setOrigin] = useState<{ message: string | null } | null>(null);
  const [network, setNetwork] = useState(false);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    const raw = tokenFromHash(window.location.hash);
    setToken(raw);
    if (!raw) {
      setView({ kind: "invalid" });
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await postJson("/api/auth/links/inspect", { token: raw });
        if (cancelled) return;
        if (!res.ok) {
          setView({ kind: "invalid" });
          return;
        }
        const link = (await res.json()) as LinkInspect;
        if (link.state === "expired") setView({ kind: "expired", purpose: link.purpose });
        else if (link.state === "used") setView({ kind: "used" });
        else setView({ kind: "form", link });
      } catch {
        if (!cancelled) setView({ kind: "invalid" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function submit(e: React.FormEvent, link: LinkInspect) {
    e.preventDefault();
    if (working) return;
    setNetwork(false);
    setOrigin(null);
    const found = passwordProblems(password, repeat, link.email);
    setProblems(found);
    if (found.length) return;
    setWorking(true);
    try {
      const res = await postJson("/api/auth/links/redeem", { token, password });
      if (res.status === 204 || res.ok) {
        window.location.assign("/");
        return;
      }
      const code = await readErrorCode(res);
      if (code === "link_used") setView({ kind: "used" });
      else if (code === "link_expired") setView({ kind: "expired", purpose: link.purpose });
      else if (code === "link_invalid") setView({ kind: "invalid" });
      else if (code === "origin_mismatch") setOrigin({ message: await readErrorMessage(res) });
      else if (res.status === 422) setProblems(["tooShort"]);
      else setNetwork(true);
    } catch {
      setNetwork(true);
    }
    setWorking(false);
  }

  const toLogin = (variant: "outline" | "ghost") => (
    <Button
      variant={variant}
      className={variant === "ghost" ? "h-9 w-full text-[13px]" : "w-full"}
      onClick={() => router.push("/login")}
      data-testid="link-to-login"
    >
      {t("goToLogin")}
    </Button>
  );

  if (view.kind === "checking") {
    return (
      <AuthCard testId="link-checking">
        <div data-purpose={purpose} className="flex flex-col items-center gap-3 py-6 text-[13px] text-on-surface-variant">
          <span className="material-symbols-outlined animate-spin text-teal" style={{ fontSize: 26 }} aria-hidden="true">
            {SPINNER_ICON}
          </span>
          <p role="status">{t("linkChecking")}</p>
        </div>
      </AuthCard>
    );
  }

  if (view.kind === "expired") {
    const smtp = !!authState?.smtp_enabled;
    const body =
      view.purpose === "invite" ? t("inviteExpiredBody") : smtp ? t("resetExpiredBody") : t("resetExpiredBodyNoMail");
    return (
      <AuthCard testId="link-expired">
        <StatusBlock icon="schedule" iconClass="text-warning" title={t("linkExpiredTitle")} body={body}>
          {view.purpose === "reset" && smtp ? (
            <>
              <Button className="mb-2 w-full" onClick={() => router.push("/forgot")} data-testid="link-request-new">
                {t("requestNewLink")}
              </Button>
              {toLogin("ghost")}
            </>
          ) : (
            toLogin("outline")
          )}
        </StatusBlock>
      </AuthCard>
    );
  }

  if (view.kind === "used") {
    return (
      <AuthCard testId="link-used">
        <StatusBlock icon="task_alt" iconClass="text-teal" title={t("linkUsedTitle")} body={t("linkUsedBody")}>
          {toLogin("outline")}
        </StatusBlock>
      </AuthCard>
    );
  }

  if (view.kind === "invalid") {
    return (
      <AuthCard testId="link-invalid">
        <StatusBlock icon="link_off" iconClass="text-warning" title={t("linkInvalidTitle")} body={t("linkInvalidBody")}>
          {toLogin("outline")}
        </StatusBlock>
      </AuthCard>
    );
  }

  const { link } = view;
  const isInvite = link.purpose === "invite";
  const passwordError = problemText(problems);
  const oidcOn = isInvite && !!authState?.oidc_enabled;

  return (
    <AuthCard testId={isInvite ? "invite-card" : "reset-card"}>
      {origin && <OriginMismatchAlert serverMessage={origin.message} />}
      {network && (
        <AuthAlert tone="warning" icon="cloud_off" testId="auth-error">
          {t("errorNetwork")}
        </AuthAlert>
      )}
      <AuthTitle>{isInvite ? t("inviteTitle") : t("resetTitle")}</AuthTitle>
      <AuthSubtitle>
        {isInvite ? t("inviteIntro", { email: link.email }) : t("resetIntro", { email: link.email })}
      </AuthSubtitle>
      <form onSubmit={(e) => submit(e, link)} noValidate>
        {/* Hidden username so password managers file the new password under the right account. */}
        <input type="email" autoComplete="username" value={link.email} readOnly hidden />
        <Field id="link-password" label={t("passwordLabel")} hint={t("passwordPolicyHint")}>
          <Input
            id="link-password"
            type="password"
            autoComplete="new-password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={problems.some((p) => p !== "mismatch")}
          />
        </Field>
        <Field id="link-repeat" label={t("passwordRepeatLabel")} error={passwordError}>
          <Input
            id="link-repeat"
            type="password"
            autoComplete="new-password"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
            error={problems.length > 0}
          />
        </Field>
        <Button type="submit" className="mt-1 w-full" disabled={working} data-testid="link-submit">
          {isInvite ? t("inviteButton") : t("resetButton")}
        </Button>
      </form>
      {oidcOn && (
        <>
          <Divider>{t("inviteOidcAlt")}</Divider>
          <Button
            type="button"
            variant="outline"
            className="h-11 w-full"
            onClick={() => window.location.assign(`${API_BASE}/api/auth/oidc/start?next=%2F`)}
            data-testid="invite-oidc"
          >
            <ButtonIcon name="key" />
            {t("oidcButton", { label: authState?.oidc_button_label ?? "" })}
          </Button>
        </>
      )}
    </AuthCard>
  );
}

const SPINNER_ICON = "progress_activity";
