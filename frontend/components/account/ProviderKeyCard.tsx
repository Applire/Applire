"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Settings → "Dein KI-Anbieter-Schlüssel" (ADR-096, WP-K). Shown only where the
 * instance asks people for their own key (`key_mode === "user"`) or a key is
 * already stored — on a default Community install nothing new appears.
 *
 * The key is write-only: the field is never pre-filled and is cleared after every
 * save attempt, successful or not. Every error code maps to a localised sentence;
 * the server's `message` is never rendered.
 */

import { useState } from "react";
import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";

import { MODEL_GUIDE_URL } from "@/components/admin/settings/ProviderCard";
import { useCurrentUser } from "@/lib/auth";
import { PROVIDER_KEY_CHANGED_EVENT } from "@/lib/provider-key-events";
import { deleteProviderKey, putProviderKey, type ProviderKeyState } from "./api";
import { ActionButton, Dialog, DialogError } from "./Dialog";
import { useProviderKey } from "./useProviderKey";

/** Brand names — not translated. */
const FAMILY_LABELS: Record<string, string> = {
  mistral: "Mistral",
  openrouter: "OpenRouter",
  requesty: "Requesty",
  anthropic: "Anthropic",
  openai: "OpenAI",
};

const TEST_REASON_KEYS: Record<string, string> = {
  key_rejected: "testKeyRejected",
  out_of_credit: "testOutOfCredit",
  model_not_found: "testModelNotFound",
  rate_limited: "testRateLimited",
  timeout: "testTimeout",
  unreachable: "testUnreachable",
};

const ERROR_KEYS: Record<string, string> = {
  invalid_body: "errInvalidBody",
  invalid_provider_family: "errInvalidFamily",
  invalid_model: "errInvalidModel",
  invalid_provider_key: "errInvalidKey",
  provider_key_secret_missing: "errSecretMissing",
  forbidden: "errForbidden",
};

/** Map a refused PUT to copy: a code (and a test-failed reason) → a catalog key. */
export function providerKeyErrorKey(code: string | null, reason: string | null): string {
  if (code === "provider_key_test_failed") return (reason && TEST_REASON_KEYS[reason]) || "testFailedOther";
  return (code && ERROR_KEYS[code]) || "errGeneric";
}

function notifyChanged() {
  window.dispatchEvent(new Event(PROVIDER_KEY_CHANGED_EVENT));
}

export function ProviderKeyCard() {
  const { user } = useCurrentUser();
  const state = useProviderKey(user !== null);
  if (!state) return null;
  if (state.key_mode !== "user" && !state.is_set && !state.unreadable) return null;
  return <ProviderKeyForm state={state} />;
}

function ProviderKeyForm({ state }: { state: ProviderKeyState }) {
  const t = useTranslations("providerKey");
  const tCommon = useTranslations("common");
  const families = state.families.length > 0 ? state.families : Object.keys(FAMILY_LABELS);
  const [family, setFamily] = useState(state.family && families.includes(state.family) ? state.family : families[0]);
  const [model, setModel] = useState(state.model ?? "");
  const [key, setKey] = useState("");
  const [test, setTest] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    let messageKey: string | null = null;
    try {
      const res = await putProviderKey({ family, model: model.trim(), key, test });
      if (!res.ok) {
        let code: string | null = null;
        let reason: string | null = null;
        try {
          const detail = ((await res.json()) as { detail?: { error_code?: unknown; reason?: unknown } }).detail;
          code = typeof detail?.error_code === "string" ? detail.error_code : null;
          reason = typeof detail?.reason === "string" ? detail.reason : null;
        } catch {
          /* no body — generic copy */
        }
        messageKey = providerKeyErrorKey(code, reason);
      }
    } catch {
      messageKey = "errGeneric";
    }
    setKey("");
    setBusy(false);
    if (messageKey) {
      setError(t(messageKey));
      return;
    }
    setNotice(t("saved"));
    notifyChanged();
  }

  async function remove() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await deleteProviderKey();
      if (!res.ok && res.status !== 404) {
        setError(t("errGeneric"));
      } else {
        setNotice(t("removed"));
        setModel("");
        notifyChanged();
      }
    } catch {
      setError(t("errGeneric"));
    }
    setConfirmRemove(false);
    setBusy(false);
  }

  const payer =
    state.payer === "instance"
      ? t("payerInstance")
      : state.payer === "user"
        ? t("payerUser", { family: FAMILY_LABELS[state.family ?? ""] ?? state.family ?? "", model: state.model ?? "" })
        : t("payerNone");
  const field = "mt-1 h-11 w-full rounded-lg border border-outline-variant bg-white px-3 text-[14px] text-on-surface";

  return (
    <section id="provider-key" data-testid="provider-key-card" className="rounded-xl bg-white p-6 shadow-soft">
      <h2 className="font-heading text-xl font-bold text-on-surface">{t("cardTitle")}</h2>
      {state.key_mode === "instance" ? (
        <p data-testid="provider-key-status" className="mt-1 text-[14px] text-on-surface-variant">
          {t("instanceModeStored")}
        </p>
      ) : (
        <p data-testid="provider-key-status" className="mt-1 text-[14px] text-on-surface-variant">
          {t("payerLead", { payer })}
        </p>
      )}
      {state.unreadable && (
        <p role="alert" data-testid="provider-key-unreadable" className="mt-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2.5 text-[13px] text-on-surface">
          {t("unreadable")}
        </p>
      )}

      <form onSubmit={(e) => void save(e)} className="mt-4 flex flex-col gap-3" autoComplete="off">
        <label className="text-[13px] font-semibold text-on-surface">
          {t("fieldFamily")}
          <select data-testid="provider-key-family" value={family} onChange={(e) => setFamily(e.target.value)} className={field}>
            {families.map((f) => (
              <option key={f} value={f}>
                {FAMILY_LABELS[f] ?? f}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[13px] font-semibold text-on-surface">
          {t("fieldModel")}
          <input
            type="text"
            required
            data-testid="provider-key-model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            autoComplete="off"
            className={field}
          />
        </label>
        <label className="text-[13px] font-semibold text-on-surface">
          {t("fieldKey")}
          <input
            type="password"
            required
            data-testid="provider-key-key"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            autoComplete="off"
            className={field}
          />
        </label>
        <label className="flex items-center gap-2 text-[13px] text-on-surface">
          <input type="checkbox" data-testid="provider-key-test" checked={test} onChange={(e) => setTest(e.target.checked)} className="accent-teal" />
          {t("testBeforeSave")}
        </label>
        {error && <DialogError testId="provider-key-error">{error}</DialogError>}
        {notice && (
          <p role="status" data-testid="provider-key-notice" className="text-[13px] text-success">
            {notice}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <ActionButton type="submit" tone="primary" data-testid="provider-key-save" disabled={busy || !model.trim() || !key}>
            {busy ? t("saving") : t("save")}
          </ActionButton>
          {state.is_set && (
            <ActionButton data-testid="provider-key-remove" disabled={busy} onClick={() => setConfirmRemove(true)}>
              {t("remove")}
            </ActionButton>
          )}
        </div>
      </form>

      <p data-testid="provider-key-fineprint" className="mt-4 text-[12.5px] leading-snug text-on-surface-variant">
        {t("finePrint")}
        <a href={MODEL_GUIDE_URL} target="_blank" rel="noopener noreferrer" className="ml-1 inline-flex items-center gap-1 font-medium text-teal hover:underline">
          {t("modelGuideLink")}
          <ExternalLink className="h-3.5 w-3.5" aria-hidden />
        </a>
      </p>

      <Dialog
        open={confirmRemove}
        onClose={() => setConfirmRemove(false)}
        title={t("removeTitle")}
        testId="provider-key-remove-dialog"
        actions={
          <>
            <ActionButton onClick={() => setConfirmRemove(false)}>{tCommon("cancel")}</ActionButton>
            <ActionButton tone="danger" data-testid="provider-key-remove-confirm" disabled={busy} onClick={() => void remove()}>
              {t("removeConfirm")}
            </ActionButton>
          </>
        }
      >
        <p>{t("removeBody")}</p>
      </Dialog>
    </section>
  );
}
