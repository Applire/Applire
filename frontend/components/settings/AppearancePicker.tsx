"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Settings → "Erscheinungsbild" (US357, ADR-023 amended 2026-10-11).
 *
 * The person picks one of the app colour schemes the admin offers, or follows
 * the instance default. The first card is always "the default" — it stores
 * `null`, so the person moves with the admin's default; the default scheme is
 * therefore not repeated as a card of its own. A choice applies at once
 * (`refreshTheme`) and persists via `PATCH /api/settings`.
 *
 * What is shown as selected is the backend's RESOLVED choice: a scheme the admin
 * deleted or stopped offering reads as "default" (ruling CS-2, SF-THEME.2).
 */

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { useTheme } from "@/components/theme-provider";
import {
  getSchemeChoices,
  setUiColorScheme,
  type OfferedScheme,
  type SchemeChoices,
} from "@/lib/api/settings";

function Swatch({ scheme }: { scheme: OfferedScheme }) {
  return (
    <div
      aria-hidden="true"
      className="w-11 h-11 rounded-lg relative flex-shrink-0 border border-outline-variant"
      style={{ background: scheme.seed_primary }}
    >
      <div
        className="absolute bottom-1 right-1 w-3.5 h-3.5 rounded-sm"
        style={{ background: scheme.seed_accent }}
      />
      <div
        className="absolute top-1 right-1 w-2.5 h-2.5 rounded-full"
        style={{ background: scheme.seed_secondary }}
      />
    </div>
  );
}

export function AppearancePicker() {
  const t = useTranslations("settings");
  const { refreshTheme } = useTheme();
  const [choices, setChoices] = useState<SchemeChoices | null>(null);
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSchemeChoices()
      .then((c) => {
        if (!cancelled) setChoices(c);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (failed) {
    return <p className="text-sm text-critical">{t("appearanceLoadFailed")}</p>;
  }
  if (!choices) {
    return <div className="h-16 w-full bg-surface-container rounded animate-pulse" />;
  }

  const defaultScheme = choices.schemes.find((s) => s.is_default) ?? null;
  const others = choices.schemes.filter((s) => !s.is_default);
  // A choice equal to the default reads as "follow the default".
  const selected =
    choices.selected_id && choices.selected_id !== choices.default_id ? choices.selected_id : null;

  async function choose(id: string | null) {
    if (saving || id === selected) return;
    setSaving(true);
    try {
      await setUiColorScheme(id);
      setChoices((c) => (c ? { ...c, selected_id: id } : c));
      refreshTheme();
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  }

  const cardClass = (active: boolean) =>
    `flex items-center gap-3 rounded-lg border p-3 text-left transition-colors disabled:opacity-60 ${
      active
        ? "border-teal ring-2 ring-teal/30 bg-teal-container-light"
        : "border-outline-variant bg-white hover:border-teal"
    }`;

  return (
    <div>
      <div
        role="radiogroup"
        aria-label={t("appearance")}
        className="grid gap-2 sm:grid-cols-2"
        data-testid="appearance-picker"
      >
        {defaultScheme && (
          <button
            type="button"
            role="radio"
            aria-checked={selected === null}
            disabled={saving}
            onClick={() => void choose(null)}
            className={cardClass(selected === null)}
            data-testid="appearance-option-default"
          >
            <Swatch scheme={defaultScheme} />
            <span className="flex flex-col">
              <span className="text-sm font-medium text-on-surface">{t("appearanceDefaultOption")}</span>
              <span className="text-xs text-on-surface-variant">
                {t("appearanceDefaultHint", { name: defaultScheme.name })}
              </span>
            </span>
          </button>
        )}
        {others.map((scheme) => (
          <button
            key={scheme.id}
            type="button"
            role="radio"
            aria-checked={selected === scheme.id}
            disabled={saving}
            onClick={() => void choose(scheme.id)}
            className={cardClass(selected === scheme.id)}
            data-testid={`appearance-option-${scheme.id}`}
          >
            <Swatch scheme={scheme} />
            <span className="text-sm font-medium text-on-surface">{scheme.name}</span>
          </button>
        ))}
      </div>
      {others.length === 0 && (
        <p className="mt-3 text-xs text-on-surface-variant">{t("appearanceNoChoices")}</p>
      )}
      {saved && <p className="mt-2 text-xs text-success">{t("saved")}</p>}
    </div>
  );
}
