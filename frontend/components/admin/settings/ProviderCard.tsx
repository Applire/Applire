"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Einstellungen → KI-Anbieter (#710; c2 mock admin-settings §1/§3/§4/§5).
 * Provider, model and key are ONE atomic write (contract §2.5) — switching and
 * entering the key in the same request. A key is write-only: the field starts
 * empty, the typed value lives in component state only until the PUT returns,
 * and is cleared then whatever the outcome. Nothing here can display a stored
 * key: the payload carries `is_set` only.
 */

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { CircleCheck, CircleAlert, Key, KeyRound, RefreshCw, ShieldAlert, ShieldCheck, CircleHelp } from "lucide-react";

import { ActionButton, Dialog } from "@/components/account/Dialog";
import { formatDate } from "@/components/account/format";
import {
  findSetting,
  providerKey,
  resetInstanceSetting,
  SETTING_KEYS,
  updateInstanceSettings,
  type AdminResult,
  type InstanceSettingsResponse,
  type ProviderStatus,
  type SettingValue,
} from "@/lib/api/admin";
import { AdminCard, NONE, SourceBadge } from "../common";

type T = ReturnType<typeof useTranslations>;

/** Map a refused write to copy (contract §7). */
export function settingsErrorText(r: AdminResult<unknown>, t: T, provider: string): string {
  if (r.ok) return "";
  if (r.kind === "error") {
    if (r.code === "provider_not_ready") return t("errorKeyRequired", { provider: String(r.detail?.provider ?? provider) });
    if (r.code === "settings_secret_unavailable") return t("errorSecretUnavailable");
    if (r.code === "invalid_setting_value" || r.code === "unknown_setting") return t("errorInvalidValue", { key: String(r.detail?.key ?? "") });
  }
  return t("errorGeneric");
}

function Qualification({ p }: { p: ProviderStatus | undefined }) {
  const t = useTranslations("adminSettings");
  const locale = useLocale();
  if (!p?.qualification) return null;
  const box = "mt-2 flex items-start gap-2 rounded-lg px-3 py-2 text-[12.5px] leading-snug";
  if (p.qualification === "qualified") {
    return (
      <div data-testid="admin-settings-qualification" data-qualification="qualified" className={`${box} bg-success-container text-on-surface`}>
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
        <div>
          <b>{t("qualifiedYes")}</b>
          {p.qualification_as_of ? [" —", t("qualifiedYesHint", { date: formatDate(p.qualification_as_of, locale) })].join(" ") : null}
        </div>
      </div>
    );
  }
  if (p.qualification === "not_qualified") {
    return (
      <div data-testid="admin-settings-qualification" data-qualification="not_qualified" className={`${box} bg-critical-container text-on-surface`}>
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-critical" aria-hidden />
        <div>
          <b>{t("qualifiedNo")}</b>
          {p.qualification_reason ? [" —", t("qualifiedNoHint", { reason: p.qualification_reason })].join(" ") : null}
        </div>
      </div>
    );
  }
  return (
    <div data-testid="admin-settings-qualification" data-qualification="unmeasured" className={`${box} bg-surface-container text-on-surface-variant`}>
      <CircleHelp className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div>
        <b>{t("qualifiedUnknown")}</b> {["—", t("qualifiedUnknownHint")].join(" ")}
      </div>
    </div>
  );
}

function Row({ label, htmlFor, children }: { label: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-2 border-t border-outline-variant/60 py-3.5 sm:grid-cols-[180px_1fr] sm:gap-5">
      <label htmlFor={htmlFor} className="text-[13.5px] font-semibold text-on-surface sm:pt-3">
        {label}
      </label>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function ProviderCard({
  data,
  onSaved,
}: {
  data: InstanceSettingsResponse;
  onSaved: (next: InstanceSettingsResponse) => void;
}) {
  const t = useTranslations("adminSettings");
  const locale = useLocale();
  const active = data.providers.find((p) => p.active)?.id ?? String(findSetting(data, SETTING_KEYS.provider)?.value ?? "");
  const [provider, setProvider] = useState(active);
  const sel = data.providers.find((p) => p.id === provider);
  const [modelDraft, setModelDraft] = useState<Record<string, string>>({});
  const model = modelDraft[provider] ?? sel?.model ?? "";
  const [keyOpen, setKeyOpen] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const providerItem = findSetting(data, SETTING_KEYS.provider);
  const modelItem = findSetting(data, providerKey(provider, "model"));
  const keyItem = findSetting(data, providerKey(provider, "key"));
  const providerChanged = provider !== active;
  const modelChanged = model.trim() !== (sel?.model ?? "");
  const dirty = providerChanged || modelChanged || newKey.length > 0;

  function cancel() {
    setProvider(active);
    setModelDraft({});
    setNewKey("");
    setKeyOpen(false);
    setError(null);
  }

  function validate(): string | null {
    if (!model.trim()) return t("errorModelRequired");
    if (sel && sel.key_required && !sel.has_key && !newKey) return t("errorKeyRequired", { provider });
    return null;
  }

  async function save() {
    const changes: Record<string, SettingValue> = {};
    if (providerChanged) changes[SETTING_KEYS.provider] = provider;
    if (modelChanged) changes[providerKey(provider, "model")] = model.trim();
    if (newKey) changes[providerKey(provider, "key")] = newKey;
    setBusy(true);
    const r = await updateInstanceSettings(changes);
    setBusy(false);
    setConfirming(false);
    setNewKey(""); // never keep a typed key past its request
    if (r.ok) {
      setKeyOpen(false);
      setModelDraft({});
      setError(null);
      onSaved(r.data);
    } else {
      setError(settingsErrorText(r, t, provider));
    }
  }

  function apply() {
    const v = validate();
    if (v) {
      setError(v);
      return;
    }
    if (providerChanged) setConfirming(true);
    else void save();
  }

  async function reset(key: string) {
    setBusy(true);
    const r = await resetInstanceSetting(key);
    setBusy(false);
    if (r.ok) {
      onSaved(r.data);
      cancel();
    } else setError(settingsErrorText(r, t, provider));
  }

  const ocr = data.dependencies.find((d) => d.code === "ocr_needs_mistral_key");
  const otherDeps = data.dependencies.filter((d) => d.code !== "ocr_needs_mistral_key");
  const lastBy = providerItem?.updated_by_email;

  return (
    <AdminCard testId="admin-settings-provider">
      <h2 className="font-manrope text-lg font-bold text-on-surface">{t("providerTitle")}</h2>
      <p className="mb-2 text-[14px] text-on-surface-variant">{t("providerHint")}</p>

      <Row label={t("providerLabel")} htmlFor="admin-provider">
        <div className="flex items-center gap-2">
          <select
            id="admin-provider"
            data-testid="admin-settings-provider-select"
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value);
              setNewKey("");
              setKeyOpen(false);
              setError(null);
            }}
            className="h-11 min-w-0 flex-1 rounded-lg border border-outline-variant bg-white px-3 text-[14px]"
          >
            {data.providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.ready ? p.id : t("providerNoKey", { provider: p.id })}
              </option>
            ))}
          </select>
          {providerItem && <SourceBadge source={providerItem.source} testId="admin-settings-provider-source" />}
        </div>
        {providerItem?.source === "panel" && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px] text-on-surface-variant">
            <span data-testid="admin-settings-provider-env">
              {providerItem.env_value !== null && providerItem.env_value !== "" ? t("envValue", { value: String(providerItem.env_value) }) : t("envValueUnset")}
            </span>
            <button type="button" data-testid="admin-settings-provider-reset" disabled={busy} onClick={() => void reset(SETTING_KEYS.provider)} className="font-medium text-teal hover:underline">
              {t("resetToEnv")}
            </button>
          </div>
        )}
        {provider === "ollama" && <p className="mt-2 text-[12.5px] text-on-surface-variant">{t("dependencyOllama")}</p>}
      </Row>

      <Row label={t("modelLabel")} htmlFor="admin-model">
        <div className="flex items-center gap-2">
          <input
            id="admin-model"
            data-testid="admin-settings-model"
            value={model}
            placeholder={t("modelPlaceholder")}
            onChange={(e) => setModelDraft((d) => ({ ...d, [provider]: e.target.value }))}
            className="h-11 min-w-0 flex-1 rounded-lg border border-outline-variant bg-white px-3 font-mono text-[13px]"
          />
          {modelItem && <SourceBadge source={modelItem.source} />}
        </div>
        {!modelChanged && <Qualification p={sel} />}
      </Row>

      <Row label={t("keyLabel")} htmlFor="admin-key">
        {!keyItem || (sel && !sel.key_required && !keyItem) ? (
          <p className="pt-2.5 text-[13.5px] text-on-surface-variant">{t("keyNone")}</p>
        ) : (
          <>
            <div data-testid="admin-settings-key-state" data-is-set={keyItem.is_set} className="flex flex-wrap items-center gap-2 pt-2 text-[13.5px]">
              {keyItem.is_set ? <Key className="h-4 w-4 text-success" aria-hidden /> : <KeyRound className="h-4 w-4 text-critical" aria-hidden />}
              <span>
                {!keyItem.is_set
                  ? sel && !sel.key_required
                    ? t("keyNone")
                    : t("keyMissing")
                  : keyItem.source === "panel"
                    ? t("keyStoredPanel", { date: keyItem.updated_at ? formatDate(keyItem.updated_at, locale) : NONE })
                    : t("keyStoredEnv")}
              </span>
              {keyItem.is_set && <SourceBadge source={keyItem.source} />}
              {keyItem.source === "panel" && (
                <button type="button" disabled={busy} onClick={() => void reset(keyItem.key)} className="text-[12.5px] font-medium text-teal hover:underline">
                  {t("resetToEnv")}
                </button>
              )}
            </div>
            {keyOpen || !keyItem.is_set ? (
              <input
                id="admin-key"
                data-testid="admin-settings-key-input"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={newKey}
                placeholder={t("keyPlaceholder")}
                onChange={(e) => setNewKey(e.target.value)}
                className="mt-2 h-11 w-full rounded-lg border border-outline-variant bg-white px-3 font-mono text-[13px]"
              />
            ) : (
              <button
                type="button"
                data-testid="admin-settings-key-replace"
                onClick={() => setKeyOpen(true)}
                className="mt-2 h-8 rounded-lg border border-outline-variant bg-white px-3 text-[12px] font-semibold"
              >
                {t("keyReplace")}
              </button>
            )}
            <p className="mt-1.5 text-[12px] text-on-surface-variant">{t("keyWriteOnly")}</p>
          </>
        )}
      </Row>

      {(ocr || otherDeps.length > 0) && (
        <Row label={t("dependencyTitle")}>
          {ocr && (
            <div data-testid="admin-settings-dep-ocr" data-satisfied={ocr.satisfied} className="rounded-lg border border-outline-variant bg-surface-dim px-3.5 py-3 text-[13px] leading-normal">
              {t("dependencyOcr")}
              <div className={`mt-1.5 flex items-center gap-1.5 text-[12.5px] font-semibold ${ocr.satisfied ? "text-success" : "text-critical"}`}>
                {ocr.satisfied ? <CircleCheck className="h-4 w-4" aria-hidden /> : <CircleAlert className="h-4 w-4" aria-hidden />}
                {ocr.satisfied ? t("dependencyOcrOk") : t("dependencyOcrMissing")}
              </div>
            </div>
          )}
          {otherDeps.map((d) => (
            <div key={d.code} className="mt-2 flex items-center gap-1.5 text-[12.5px]">
              {d.satisfied ? <CircleCheck className="h-4 w-4 text-success" aria-hidden /> : <CircleAlert className="h-4 w-4 text-critical" aria-hidden />}
              {[t("dependencyGeneric", { code: d.code }), d.keys.join(", ")].join(" — ")}
            </div>
          ))}
        </Row>
      )}

      <div className="my-3.5 flex gap-2 rounded-lg bg-surface-container px-3 py-2.5 text-[12.5px] leading-normal text-on-surface-variant">
        <RefreshCw className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        {t("inFlight")}
      </div>

      {error && (
        <p data-testid="admin-settings-provider-error" role="alert" className="mb-3 text-[12.5px] font-medium text-critical">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-[12px] text-on-surface-variant">
          {providerItem?.source === "panel" && providerItem.updated_at && lastBy
            ? t("lastChange", { date: formatDate(providerItem.updated_at, locale), email: lastBy })
            : null}
        </span>
        <span className="flex gap-2">
          <button type="button" disabled={!dirty || busy} onClick={cancel} className="h-10 rounded-lg border border-outline-variant bg-white px-4 text-[13px] font-semibold disabled:opacity-50">
            {t("cancel")}
          </button>
          <button
            type="button"
            data-testid="admin-settings-provider-apply"
            disabled={!dirty || busy}
            onClick={apply}
            className="h-10 rounded-lg bg-teal px-4 text-[13px] font-semibold text-white shadow-soft disabled:opacity-50"
          >
            {t("save")}
          </button>
        </span>
      </div>

      <Dialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title={t("switchTitle", { provider })}
        testId="admin-settings-switch-dialog"
        actions={
          <>
            <ActionButton onClick={() => setConfirming(false)}>{t("cancel")}</ActionButton>
            <ActionButton tone="primary" data-testid="admin-settings-switch-confirm" disabled={busy} onClick={() => void save()}>
              {t("switchConfirm")}
            </ActionButton>
          </>
        }
      >
        <p>{t("switchBody", { provider, model: model.trim() })}</p>
      </Dialog>
    </AdminCard>
  );
}
