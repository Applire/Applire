"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Administration → Einstellungen (c2 mock admin-settings.html): three cards on
 * one page — KI-Anbieter (#710), Stellenanzeigen von LinkedIn (#726) and
 * Automatisches Löschen (#738). Every value shows its source (ADR-093 cl. 3); a
 * panel value can be reset to the `.env` value. Writes are session-only on the
 * backend (contract §1) and land in the audit log.
 */

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { AlertTriangle, History, ShieldCheck } from "lucide-react";

import { ActionButton, Dialog } from "@/components/account/Dialog";
import { formatDate } from "@/components/account/format";
import {
  findSetting,
  resetInstanceSetting,
  SETTING_KEYS,
  updateInstanceSettings,
  type AdminDashboardResponse,
  type InstanceSettingsResponse,
} from "@/lib/api/admin";
import { useAdminDashboard, useInstanceSettings } from "@/lib/api/admin-hooks";
import { AdminCard, AdminForbidden, LoadFailed, Loading, SourceBadge } from "../common";
import { ProviderCard, settingsErrorText } from "./ProviderCard";
import { Toggle } from "./Toggle";

function ToggleRow({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="my-3 flex items-center justify-between gap-4 rounded-lg bg-surface-dim px-4 py-3.5">
      <b id={id} className="text-[14px] font-semibold text-on-surface">
        {label}
      </b>
      <span className="flex items-center gap-2">{children}</span>
    </div>
  );
}

function useBoolSetting(data: InstanceSettingsResponse, key: string, onSaved: (d: InstanceSettingsResponse) => void) {
  const t = useTranslations("adminSettings");
  const item = findSetting(data, key);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function set(next: boolean) {
    setBusy(true);
    const r = await updateInstanceSettings({ [key]: next });
    setBusy(false);
    if (r.ok) {
      setError(null);
      onSaved(r.data);
    } else setError(settingsErrorText(r, t, ""));
  }
  async function reset() {
    setBusy(true);
    const r = await resetInstanceSetting(key);
    setBusy(false);
    if (r.ok) onSaved(r.data);
    else setError(settingsErrorText(r, t, ""));
  }
  return { item, value: item?.value === true, busy, error, set, reset };
}

function ResetLink({ onClick, busy, testId }: { onClick: () => void; busy: boolean; testId: string }) {
  const t = useTranslations("adminSettings");
  return (
    <button type="button" data-testid={testId} disabled={busy} onClick={onClick} className="text-[12.5px] font-medium text-teal hover:underline">
      {t("resetToEnv")}
    </button>
  );
}

function LinkedinCard({ data, onSaved }: { data: InstanceSettingsResponse; onSaved: (d: InstanceSettingsResponse) => void }) {
  const t = useTranslations("adminSettings");
  const s = useBoolSetting(data, SETTING_KEYS.linkedin, onSaved);
  if (!s.item) return null;
  return (
    <AdminCard testId="admin-settings-linkedin">
      <h2 className="font-manrope text-lg font-bold text-on-surface">{t("linkedinTitle")}</h2>
      <ToggleRow id="admin-linkedin-label" label={t("linkedinToggle")}>
        <SourceBadge source={s.item.source} testId="admin-settings-linkedin-source" />
        <Toggle checked={s.value} disabled={s.busy} onChange={(v) => void s.set(v)} labelledBy="admin-linkedin-label" testId="admin-settings-linkedin-toggle" />
      </ToggleRow>
      <p data-testid="admin-settings-linkedin-state" className="mb-1.5 text-[13.5px] leading-normal">
        {s.value ? t("linkedinOn") : t("linkedinOff")}
      </p>
      <p className="text-[12px] text-on-surface-variant">{t("linkedinDecide")}</p>
      {s.item.source === "panel" && (
        <div className="mt-2">
          <ResetLink onClick={() => void s.reset()} busy={s.busy} testId="admin-settings-linkedin-reset" />
        </div>
      )}
      {s.error && (
        <p role="alert" className="mt-2 text-[12.5px] font-medium text-critical">
          {s.error}
        </p>
      )}
    </AdminCard>
  );
}

function RetentionCard({
  data,
  dash,
  onSaved,
}: {
  data: InstanceSettingsResponse;
  dash: AdminDashboardResponse | null;
  onSaved: (d: InstanceSettingsResponse) => void;
}) {
  const t = useTranslations("adminSettings");
  const locale = useLocale();
  const s = useBoolSetting(data, SETTING_KEYS.retention, onSaved);
  const [confirmOff, setConfirmOff] = useState(false);
  if (!s.item) return null;
  const others = dash ? Math.max(0, dash.users.total - 1) : 0;
  const ttl = dash?.retention.ttl_days;
  const since = dash?.retention.enabled_since ?? null;
  const by = dash?.retention.changed_by_email ?? null;
  // adv-admin ADM-1: the WORKER's newest run skipped (its own environment, an
  // unobserved flip) — say so even while the panel reads "on".
  const skippedAt = dash?.retention.last_run_skipped ? dash.retention.last_run_at : null;
  const ttlItems: [string, number][] = ttl
    ? [
        ["ttlUploads", ttl.uploads],
        ["ttlInterviews", ttl.interview_sessions],
        ["ttlDocuments", ttl.generated_documents],
        ["ttlProfiles", ttl.profile_inactivity],
      ]
    : [];
  // Ruling C1-3: these keep running whatever the switch says, so they are
  // listed apart from it (E2E finding E2E-2).
  const alwaysItems: [string, number][] = ttl
    ? [
        ["ttlCancelled", ttl.cancelled_applications],
        ["ttlAudit", ttl.audit_log],
      ]
    : [];

  return (
    <AdminCard testId="admin-settings-retention">
      <h2 className="font-manrope text-lg font-bold text-on-surface">{t("retentionTitle")}</h2>
      <ToggleRow id="admin-retention-label" label={t("retentionToggle")}>
        <SourceBadge source={s.item.source} testId="admin-settings-retention-source" />
        <Toggle
          checked={s.value}
          disabled={s.busy}
          onChange={(v) => (v ? void s.set(true) : setConfirmOff(true))}
          labelledBy="admin-retention-label"
          testId="admin-settings-retention-toggle"
        />
      </ToggleRow>

      {s.value ? (
        <div data-testid="admin-settings-retention-on">
          {ttlItems.length > 0 ? (
            <>
              <p className="text-[13.5px]">{t("retentionOnIntro")}</p>
              <ul className="my-1.5 list-disc pl-5 text-[13px] leading-7 text-on-surface">
                {ttlItems
                  .filter(([, d]) => d > 0)
                  .map(([k, d]) => (
                    <li key={k}>{t(k, { days: d })}</li>
                  ))}
              </ul>
            </>
          ) : null}

          {skippedAt && (
            <p
              data-testid="admin-settings-retention-last-run-skipped"
              className="mt-1.5 flex items-center gap-2 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-[12.5px] text-on-surface"
            >
              <AlertTriangle className="h-4 w-4" aria-hidden />
              {t("retentionLastRunSkipped", { date: formatDate(skippedAt, locale) })}
            </p>
          )}
          {since && (
            <p data-testid="admin-settings-retention-proof" className="mt-1.5 flex items-center gap-2 text-[12.5px] text-success">
              <ShieldCheck className="h-4 w-4" aria-hidden />
              {t("retentionProofOn", { date: formatDate(since, locale) })}
            </p>
          )}
        </div>
      ) : (
        <div data-testid="admin-settings-retention-off">
          {others > 0 && (
            <div data-testid="admin-settings-retention-others" className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3.5 py-3 text-[13px] text-neutral-dark">
              {t("retentionOthers", { count: others })}
            </div>
          )}
          <p className="text-[13.5px]">{t("retentionOffIntro")}</p>
          <ul className="my-1.5 list-disc pl-5 text-[13px] leading-7 text-on-surface">
            <li>{t("keepsSelfDelete")}</li>
            <li>{t("keepsAdminDelete")}</li>
            <li>{t("keepsLog")}</li>
          </ul>
          {s.item.updated_at && (by || s.item.updated_by_email) && (
            <p className="mt-1.5 flex items-center gap-2 text-[12.5px] text-gold-dim">
              <History className="h-4 w-4" aria-hidden />
              {t("retentionProofOff", { date: formatDate(s.item.updated_at, locale), email: String(by ?? s.item.updated_by_email) })}
            </p>
          )}
        </div>
      )}

      <div data-testid="admin-settings-retention-always" className="mt-3">
        <p className="text-[13.5px]">{t("retentionAlwaysIntro")}</p>
        <ul className="my-1.5 list-disc pl-5 text-[13px] leading-7 text-on-surface">
          {alwaysItems
            .filter(([, d]) => d > 0)
            .map(([k, d]) => (
              <li key={k}>{t(k, { days: d })}</li>
            ))}
          <li>{t("alwaysAuthLinks")}</li>
        </ul>
        <p className="text-[12px] text-on-surface-variant">{t("ttlEnvNote")}</p>
      </div>
      {s.item.source === "panel" && (
        <div className="mt-2">
          <ResetLink onClick={() => void s.reset()} busy={s.busy} testId="admin-settings-retention-reset" />
        </div>
      )}
      {s.error && (
        <p role="alert" className="mt-2 text-[12.5px] font-medium text-critical">
          {s.error}
        </p>
      )}

      <Dialog
        open={confirmOff}
        onClose={() => setConfirmOff(false)}
        title={t("retentionOffTitle")}
        testId="admin-settings-retention-dialog"
        actions={
          <>
            <ActionButton onClick={() => setConfirmOff(false)}>{t("cancel")}</ActionButton>
            <ActionButton
              tone="danger"
              data-testid="admin-settings-retention-confirm"
              disabled={s.busy}
              onClick={() => {
                setConfirmOff(false);
                void s.set(false);
              }}
            >
              {t("retentionOffConfirm")}
            </ActionButton>
          </>
        }
      >
        <p>{t("retentionOffBody")}</p>
      </Dialog>
    </AdminCard>
  );
}

export function InstanceSettings() {
  const t = useTranslations("adminSettings");
  const settings = useInstanceSettings();
  const dash = useAdminDashboard();
  const [savedAt, setSavedAt] = useState(0);
  useEffect(() => {
    if (!savedAt) return;
    const id = setTimeout(() => setSavedAt(0), 4000);
    return () => clearTimeout(id);
  }, [savedAt]);

  if (settings.status === "forbidden") return <AdminForbidden />;
  if (settings.status === "error") return <LoadFailed testId="admin-settings-load-failed" onRetry={settings.reload} />;
  if (settings.status === "loading") return <Loading />;

  const onSaved = (d: InstanceSettingsResponse) => {
    settings.setData(d);
    dash.reload();
    setSavedAt(Date.now());
  };
  return (
    <div data-testid="admin-settings" className="flex flex-col gap-4">
      <p className="text-[14px] text-on-surface-variant">{t("intro")}</p>
      {/* key = the effective provider: a save or reset re-seeds the form from the server */}
      <ProviderCard key={settings.data.providers.find((p) => p.active)?.id ?? ""} data={settings.data} onSaved={onSaved} />
      <LinkedinCard data={settings.data} onSaved={onSaved} />
      <RetentionCard data={settings.data} dash={dash.status === "ready" ? dash.data : null} onSaved={onSaved} />
      {savedAt > 0 && (
        <div data-testid="admin-settings-saved" role="status" className="fixed bottom-6 right-6 z-[70] rounded-lg bg-neutral-dark px-3.5 py-2.5 text-[13px] text-white shadow-card">
          {t("saved")}
        </div>
      )}
    </div>
  );
}
