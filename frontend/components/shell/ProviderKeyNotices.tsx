"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * ADR-096 onboarding notices of the signed-in shell:
 *  - `ProviderKeyBanner`: the instance asks for per-user keys (`key_mode ===
 *    "user"`) and this person has neither a key nor a grant (`payer === "none"`).
 *  - `ProviderKeyDialog`: any API call just answered `409 provider_key_required |
 *    provider_key_unreadable` (event raised by the global fetch patch).
 * Both gate on the KNOWN signed-in user: a person-scoped call from an
 * always-mounted component must not run signed out (global 401 redirect).
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { ActionButton, Dialog } from "@/components/account/Dialog";
import { useProviderKey } from "@/components/account/useProviderKey";
import {
  PROVIDER_KEY_REQUIRED_EVENT,
  isProviderKeyErrorCode,
  type ProviderKeyErrorCode,
} from "@/lib/provider-key-events";
import { useShellUser } from "./ShellUserContext";

export function ProviderKeyBanner() {
  const t = useTranslations("providerKey");
  const router = useRouter();
  const { id } = useShellUser();
  const state = useProviderKey(id !== null);
  if (!state || state.key_mode !== "user" || state.payer !== "none") return null;
  return (
    <div
      role="status"
      data-testid="provider-key-banner"
      className="flex flex-shrink-0 flex-wrap items-center justify-center gap-3 border-b border-warning/40 bg-warning-container px-4 py-2 text-center text-[13px] text-on-surface"
    >
      <span>{t("needKey")}</span>
      <ActionButton tone="primary" data-testid="provider-key-banner-add" onClick={() => router.push("/settings#provider-key")}>
        {t("addKey")}
      </ActionButton>
    </div>
  );
}

export function ProviderKeyDialog() {
  const t = useTranslations("providerKey");
  const router = useRouter();
  const [code, setCode] = useState<ProviderKeyErrorCode | null>(null);

  useEffect(() => {
    const onRequired = (e: Event) => {
      const c = (e as CustomEvent<{ code?: string }>).detail?.code;
      if (isProviderKeyErrorCode(c)) setCode(c);
    };
    window.addEventListener(PROVIDER_KEY_REQUIRED_EVENT, onRequired);
    return () => window.removeEventListener(PROVIDER_KEY_REQUIRED_EVENT, onRequired);
  }, []);

  const close = () => setCode(null);
  const unreadable = code === "provider_key_unreadable";
  return (
    <Dialog
      open={code !== null}
      onClose={close}
      title={t("dialogTitle")}
      testId="provider-key-dialog"
      actions={
        <>
          <ActionButton data-testid="provider-key-dialog-close" onClick={close}>
            {t("dialogClose")}
          </ActionButton>
          <ActionButton
            tone="primary"
            data-testid="provider-key-dialog-add"
            onClick={() => {
              close();
              router.push("/settings#provider-key");
            }}
          >
            {t("addKey")}
          </ActionButton>
        </>
      }
    >
      <p>{unreadable ? t("needKeyUnreadable") : t("needKey")}</p>
    </Dialog>
  );
}
