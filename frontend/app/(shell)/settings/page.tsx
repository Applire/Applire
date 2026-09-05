"use client";

// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.


import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useLocale } from "@/lib/providers/locale-provider";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { TargetPagesSelect } from "@/components/cv/TargetPagesSelect";
import { getSettings, setReviewMode, setTargetCvPages } from "@/lib/api/settings";
import {
  REVIEW_MODE_PREFERENCES,
  type ReviewModePreference,
} from "@/lib/review-walked";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

function DefaultColorPicker() {
  const t = useTranslations("settings");
  const tCommon = useTranslations("common");
  const [hex, setHex] = useState("#2b5fa8");
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API_BASE}/api/settings`)
      .then((r) => r.json())
      .then((d) => { if (d.default_accent_hex) setHex(d.default_accent_hex); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    const res = await fetch(`${API_BASE}/api/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default_accent_hex: hex }),
    });
    if (res.ok) setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  if (loading) return <div className="h-8 w-32 bg-surface-container rounded animate-pulse" />;

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2 bg-surface-container border border-neutral-medium rounded px-2 py-1.5">
        <div className="w-5 h-5 rounded border border-neutral-medium" style={{ background: hex }} />
        <input
          type="text"
          value={hex}
          onChange={(e) => { if (/^#[0-9a-fA-F]{0,6}$/.test(e.target.value)) setHex(e.target.value); }}
          className="text-sm font-mono bg-transparent outline-none w-20"
          maxLength={7}
        />
      </div>
      <button
        type="button"
        onClick={handleSave}
        className="px-3 py-1.5 text-sm font-medium bg-teal text-white rounded hover:opacity-90"
      >
        {saved ? t("saved") : tCommon("save")}
      </button>
    </div>
  );
}

// E042/US239 (ADR-051 §1): the persisted default CV page target — mirrors
// the flow CV step's per-generation selector, plus a "region standard"
// (null) choice that clears the override.
function DefaultCVLengthPicker() {
  const t = useTranslations("settings");
  const [value, setValue] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (!cancelled) setValue(s.target_cv_pages);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleChange = async (next: number | null) => {
    setValue(next);
    try {
      await setTargetCvPages(next);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      // Best-effort — the selector still reflects the chosen value locally.
    }
  };

  if (loading) return <div className="h-8 w-40 bg-surface-container rounded animate-pulse" />;

  return (
    <div className="flex items-center gap-3">
      <TargetPagesSelect
        value={value}
        onChange={(v) => void handleChange(v)}
        allowRegionStandard
        className="flex flex-col items-start gap-1"
      />
      {saved && <span className="text-xs text-success">{t("saved")}</span>}
    </div>
  );
}

/**
 * E058/US301 (ADR-081 cl. 5) — the review-mode preference.
 *
 * Three values, and the default is deliberately NOT a fixed layout: under
 * `auto` the mode is a property of the DOCUMENT, not of the person. Guided
 * answers *"walk me through what is new"*, which is true after a fresh
 * generation and false on the fifth visit to the same document — for a power
 * user exactly as much as for a newcomer. The two fixed values exist for people
 * who want the mode settled regardless.
 *
 * Deliberately NOT on the agent door (cl. 8, an explicit SF-DOOR.4 carve-out).
 */
function ReviewModePicker() {
  const t = useTranslations("settings");
  const [value, setValue] = useState<ReviewModePreference>("auto");
  const [loading, setLoading] = useState(true);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (!cancelled) setValue(s.review_mode ?? "auto");
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const LABEL_KEY: Record<ReviewModePreference, string> = {
    auto: "reviewModeAuto",
    overview: "reviewModeOverview",
    guided: "reviewModeGuided",
  };

  const handleChange = async (next: ReviewModePreference) => {
    setValue(next);
    try {
      await setReviewMode(next);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      // Best-effort — the selector still reflects the chosen value locally.
    }
  };

  if (loading) return <div className="h-8 w-40 bg-surface-container rounded animate-pulse" />;

  return (
    <div className="flex items-center gap-3">
      <select
        data-testid="settings-review-mode"
        aria-label={t("reviewMode")}
        value={value}
        onChange={(e) => void handleChange(e.target.value as ReviewModePreference)}
        className="rounded-lg border border-outline-variant bg-surface-bright px-3 py-2 text-sm text-on-surface"
      >
        {REVIEW_MODE_PREFERENCES.map((mode) => (
          <option key={mode} value={mode}>
            {t(LABEL_KEY[mode])}
          </option>
        ))}
      </select>
      {saved && <span className="text-xs text-success">{t("saved")}</span>}
    </div>
  );
}

function LanguageSwitcher() {
  const t = useTranslations("settings");
  const { locale, setLocale } = useLocale();
  const [saving, setSaving] = useState(false);

  async function handleSwitch(lang: "de" | "en") {
    if (lang === locale) return;
    setSaving(true);
    await setLocale(lang);
    setSaving(false);
  }

  return (
    <section className="rounded-lg border border-neutral-medium p-4">
      <h2 className="text-base font-semibold text-neutral-dark mb-1">{t("language")}</h2>
      <p className="text-sm text-neutral-medium mb-4">{t("languageHint")}</p>
      <div className="flex gap-2" aria-disabled={saving}>
        {(["de", "en"] as const).map((lang) => (
          <button
            key={lang}
            type="button"
            onClick={() => void handleSwitch(lang)}
            disabled={saving}
            className={`px-4 py-1.5 text-sm font-medium rounded border transition-colors ${
              locale === lang
                ? "bg-teal text-white border-teal"
                : "bg-white text-neutral-dark border-neutral-medium hover:border-teal"
            }`}
            data-testid={`lang-switch-${lang}`}
          >
            {lang.toUpperCase()}
          </button>
        ))}
      </div>
    </section>
  );
}

export default function SettingsPage() {
  const t = useTranslations("settings");
  const tErrors = useTranslations("errors");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const [deleting, setDeleting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");

  const handleExport = async () => {
    setExporting(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/profile/export`);
      if (res.ok) {
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "applire-export.json";
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      } else {
        const err = await res.json();
        setError(err.detail || tErrors("exportFailed"));
      }
    } catch {
      setError(tErrors("exportFailed"));
    } finally {
      setExporting(false);
    }
  };

  const handleDelete = async () => {
    if (deleteConfirmation !== "DELETE") {
      setError(tErrors("typeDeleteToConfirm"));
      return;
    }
    setDeleting(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/profile`, { method: "DELETE" });
      if (res.status === 202 || res.ok) {
        router.push("/");
      } else {
        const err = await res.json();
        setError(err.detail || tErrors("deletionFailed"));
      }
    } catch {
      setError(tErrors("deletionFailed"));
    } finally {
      setDeleting(false);
      setShowDeleteConfirm(false);
      setDeleteConfirmation("");
    }
  };

  return (
    <div className="flex flex-col flex-1 overflow-hidden bg-surface-dim">
      <AppTopbar mode="section" titleKey="shell.settings" />

      <main className="flex-1 overflow-y-auto px-4 py-8">
        <div className="max-w-4xl mx-auto space-y-6">
          {error && (
            <div className="p-4 rounded-lg bg-critical/10 border border-critical/20">
              <p className="text-sm text-critical">{error}</p>
            </div>
          )}

          {/* Language switcher */}
          <LanguageSwitcher />

          {/* GDPR Section */}
          <Card className="p-6">
            <h2 className="font-heading text-xl font-bold text-neutral-dark mb-4">
              {t("dataPrivacy")}
            </h2>
            <p className="text-sm text-gray-500 mb-6">{t("gdprHint")}</p>

            <div className="space-y-4">
              <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                <div>
                  <h3 className="font-medium text-neutral-dark">{t("exportMyData")}</h3>
                  <p className="text-sm text-gray-500">{t("exportHint")}</p>
                </div>
                <Button variant="outline" onClick={handleExport} disabled={exporting}>
                  {exporting ? t("exporting") : tCommon("export")}
                </Button>
              </div>

              <div className="flex items-center justify-between p-4 bg-critical/5 rounded-lg border border-critical/20">
                <div>
                  <h3 className="font-medium text-critical">{t("deleteAllData")}</h3>
                  <p className="text-sm text-gray-500">{t("deleteHint")}</p>
                </div>
                <Button variant="destructive" onClick={() => setShowDeleteConfirm(true)} disabled={deleting}>
                  {tCommon("delete")}
                </Button>
              </div>
            </div>
          </Card>

          {/* Default CV Color */}
          <section className="rounded-lg border border-neutral-medium p-4">
            <h2 className="text-base font-semibold text-neutral-dark mb-1">{t("defaultCVColor")}</h2>
            <p className="text-sm text-neutral-medium mb-4">{t("defaultCVColorHint")}</p>
            <DefaultColorPicker />
          </section>

          {/* Default CV Length (E042/US239, ADR-051) */}
          <section className="rounded-lg border border-neutral-medium p-4">
            <h2 className="text-base font-semibold text-neutral-dark mb-1">{t("defaultCVLength")}</h2>
            <p className="text-sm text-neutral-medium mb-4">{t("defaultCVLengthHint")}</p>
            <DefaultCVLengthPicker />
          </section>

          {/* E058/US301 (ADR-081 cl. 5) */}
          <section data-testid="settings-review-mode-section">
            <h2 className="text-base font-semibold text-neutral-dark mb-1">{t("reviewMode")}</h2>
            <p className="text-sm text-neutral-medium mb-4">{t("reviewModeHint")}</p>
            <ReviewModePicker />
          </section>

          {/* Delete Confirmation */}
          {showDeleteConfirm && (
            <Card className="p-6 border-2 border-critical/30">
              <h3 className="font-heading text-lg font-bold text-critical mb-2">
                {t("confirmDeletion")}
              </h3>
              <p className="text-sm text-gray-600 mb-4">{t("deletionIrreversible")}</p>
              <ul className="text-sm text-gray-600 list-disc list-inside mb-4 space-y-1">
                <li>{t("deletionItemMasterProfile")}</li>
                <li>{t("deletionItemApplications")}</li>
                <li>{t("deletionItemInterviews")}</li>
                <li>{t("deletionItemCVs")}</li>
              </ul>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {t("typeDeleteLabel", { token: t("deleteToken") })} <code className="bg-gray-100 px-1 rounded">{t("deleteToken")}</code>
                </label>
                <input
                  type="text"
                  value={deleteConfirmation}
                  onChange={(e) => setDeleteConfirmation(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-4 py-2 text-sm focus:border-critical focus:outline-none focus:ring-2 focus:ring-critical/20"
                  placeholder={t("deleteToken")}
                />
              </div>
              <div className="flex gap-2">
                <Button
                  variant="destructive"
                  onClick={handleDelete}
                  disabled={deleting || deleteConfirmation !== "DELETE"}
                >
                  {deleting ? t("deleting") : t("confirmDeleteButton")}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => { setShowDeleteConfirm(false); setDeleteConfirmation(""); setError(""); }}
                >
                  {tCommon("cancel")}
                </Button>
              </div>
            </Card>
          )}
        </div>
      </main>

    </div>
  );
}
