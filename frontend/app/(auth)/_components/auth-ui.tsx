// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * US330 — building blocks shared by the signed-out pages (/login, /setup,
 * /forgot, /invite, /reset). Drawn after the approved W0-B mocks (G-1):
 * a 420 px white card on `surface-dim`, the Applire mark, alerts in four tones.
 */

import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

export function AuthCard({ children, testId }: { children: React.ReactNode; testId?: string }) {
  const t = useTranslations("shell");
  return (
    <div
      data-testid={testId}
      className="w-full max-w-[420px] rounded-xl bg-white p-6 shadow-card sm:p-8"
    >
      <div className="mb-6 flex items-center gap-2.5">
        <img
          src="/applire-icon.png"
          alt={t("appLogoAlt")}
          className="h-[34px] w-[34px] rounded-[9px] object-contain"
        />
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- brand name not translated */}
        <span className="font-manrope text-[16px] font-extrabold tracking-tight text-primary">Applire</span>
      </div>
      {children}
    </div>
  );
}

export function AuthTitle({ children, center }: { children: React.ReactNode; center?: boolean }) {
  return (
    <h1 className={cn("mb-1.5 font-manrope text-[22px] font-extrabold text-on-surface", center && "text-center")}>
      {children}
    </h1>
  );
}

export function AuthSubtitle({ children }: { children: React.ReactNode }) {
  return <p className="mb-5 text-sm leading-relaxed text-on-surface-variant">{children}</p>;
}

type Tone = "critical" | "warning" | "info" | "success";

const TONE: Record<Tone, string> = {
  critical: "bg-critical/5 border-critical/25 text-[#9b2c2c]",
  warning: "bg-warning-container border-warning/40 text-[#7a5200]",
  info: "bg-teal-container border-outline-variant text-teal-dim",
  success: "bg-success-container border-success/35 text-[#1d6b4a]",
};

export function AuthAlert({
  tone,
  icon,
  children,
  testId,
}: {
  tone: Tone;
  icon: string;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <div
      role={tone === "critical" || tone === "warning" ? "alert" : "status"}
      data-testid={testId}
      data-tone={tone}
      className={cn("mb-4 flex gap-2.5 rounded-lg border px-3.5 py-3 text-[13px] leading-normal", TONE[tone])}
    >
      <span className="material-symbols-outlined mt-px flex-shrink-0" style={{ fontSize: 18 }} aria-hidden="true">
        {icon}
      </span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/** A shell command shown verbatim (dark block). */
export function CommandBlock({ children }: { children: string }) {
  return (
    <code className="my-2 block whitespace-pre-wrap break-all rounded-lg bg-neutral-dark px-3 py-2.5 font-mono text-[12px] leading-relaxed text-teal-container">
      {children}
    </code>
  );
}

export function Field({
  id,
  label,
  children,
  hint,
  error,
}: {
  id: string;
  label: string;
  children: React.ReactNode;
  hint?: string;
  error?: string | null;
}) {
  return (
    <div className="mb-4">
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-gray-700">
        {label}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="mt-1.5 text-xs font-medium text-critical">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="mt-1.5 text-xs leading-snug text-gray-500">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function Divider({ children }: { children: React.ReactNode }) {
  return (
    <div className="my-4 flex items-center gap-3 text-xs text-gray-400 before:h-px before:flex-1 before:bg-gray-200 after:h-px after:flex-1 after:bg-gray-200">
      {children}
    </div>
  );
}

/** Centered status card body: icon, title, text, actions (expired / used / invalid link). */
export function StatusBlock({
  icon,
  iconClass,
  title,
  body,
  children,
  testId,
}: {
  icon: string;
  iconClass: string;
  title: string;
  body: string;
  children?: React.ReactNode;
  testId?: string;
}) {
  return (
    <div className="text-center" data-testid={testId}>
      <span className={cn("material-symbols-outlined mb-2", iconClass)} style={{ fontSize: 30 }} aria-hidden="true">
        {icon}
      </span>
      <AuthTitle center>{title}</AuthTitle>
      <p className="mb-5 text-[13px] leading-relaxed text-on-surface-variant">{body}</p>
      {children}
    </div>
  );
}

/** Material icon used as an inline glyph in a button. */
export function ButtonIcon({ name }: { name: string }) {
  return (
    <span className="material-symbols-outlined" style={{ fontSize: 18 }} aria-hidden="true">
      {name}
    </span>
  );
}

/** Client-side password rules (contract §1: 12–256, not the account email). */
export type PasswordProblem = "tooShort" | "tooLong" | "mismatch" | "isEmail";

export function passwordProblems(password: string, repeat: string, email: string): PasswordProblem[] {
  const out: PasswordProblem[] = [];
  if (password.length < 12) out.push("tooShort");
  else if (password.length > 256) out.push("tooLong");
  if (email && password.trim().toLowerCase() === email.trim().toLowerCase()) out.push("isEmail");
  if (password !== repeat) out.push("mismatch");
  return out;
}

const PROBLEM_KEY: Record<PasswordProblem, string> = {
  tooShort: "errorPasswordTooShort",
  tooLong: "errorPasswordTooLong",
  mismatch: "errorPasswordMismatch",
  isEmail: "errorPasswordIsEmail",
};

export function usePasswordProblemText(): (problems: PasswordProblem[]) => string | null {
  const t = useTranslations("auth");
  return (problems) => (problems.length ? problems.map((p) => t(PROBLEM_KEY[p])).join(" ") : null);
}

/** The server's English `origin_mismatch` message under the translated title + hint. */
export function OriginMismatchAlert({ serverMessage }: { serverMessage: string | null }) {
  const t = useTranslations("auth");
  return (
    <AuthAlert tone="critical" icon="gpp_bad" testId="auth-error-origin">
      <p className="font-semibold">{t("errorOriginTitle")}</p>
      <p>{t("errorOriginHint")}</p>
      {serverMessage ? (
        <code className="mt-1.5 block rounded-md bg-white/70 px-2 py-1.5 font-mono text-[12px] leading-snug text-[#5b1d1d]">
          {serverMessage}
        </code>
      ) : null}
    </AuthAlert>
  );
}
