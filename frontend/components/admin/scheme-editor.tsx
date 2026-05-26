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


import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { deriveScheme, type DerivedScheme, type SeedColors } from "@/lib/theme";
import { useTheme } from "@/components/theme-provider";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

function extractError(body: unknown, fallback: string): string {
  if (typeof body !== "object" || body === null) return fallback;
  const d = (body as Record<string, unknown>).detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d.length > 0) {
    return (d as Array<{ msg?: string }>).map((e) => e.msg ?? "").filter(Boolean).join("; ") || fallback;
  }
  return fallback;
}

interface SavedScheme {
  id: string;
  name: string;
  is_active: boolean;
  is_builtin: boolean;
  seed_primary: string;
  seed_accent: string;
  seed_secondary: string;
  surface_lightness: number;
  derived: Record<string, string>;
}

const NEUTRAL_DEFAULTS: SeedColors = {
  primary: "#4a4a4a",
  accent: "#4a4a4a",
  secondary: "#4a4a4a",
};

const SURFACE_LIGHTNESS_DEFAULT = 0.95;

export function SchemeEditor() {
  const { refreshTheme } = useTheme();
  const t = useTranslations("admin.schemeEditor");
  const [schemes, setSchemes] = useState<SavedScheme[]>([]);
  const [seeds, setSeeds] = useState<SeedColors>(NEUTRAL_DEFAULTS);
  const [surfaceLightness, setSurfaceLightness] = useState(SURFACE_LIGHTNESS_DEFAULT);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [activating, setActivating] = useState(false);
  const [error, setError] = useState("");

  const fetchSchemes = useCallback(async () => {
    const res = await fetch(`${API_BASE}/api/admin/color-schemes`);
    if (res.ok) setSchemes(await res.json());
  }, []);

  useEffect(() => { fetchSchemes(); }, [fetchSchemes]);

  // Apply derived colors to preview via CSS custom properties on every change
  useEffect(() => {
    const derived: DerivedScheme = deriveScheme(seeds, surfaceLightness);
    for (const [key, value] of Object.entries(derived)) {
      document.documentElement.style.setProperty(key, value);
    }
  }, [seeds, surfaceLightness]);

  function loadScheme(scheme: SavedScheme) {
    setSeeds({
      primary: scheme.seed_primary,
      accent: scheme.seed_accent,
      secondary: scheme.seed_secondary,
    });
    setSurfaceLightness(scheme.surface_lightness);
    setName(scheme.name);
  }

  function startNew() {
    setSeeds(NEUTRAL_DEFAULTS);
    setSurfaceLightness(SURFACE_LIGHTNESS_DEFAULT);
    setName("");
  }

  async function handleSave() {
    if (!name.trim()) { setError(t("errorNameRequired")); return; }
    setSaving(true); setError("");
    try {
      const res = await fetch(`${API_BASE}/api/admin/color-schemes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          seed_primary: seeds.primary,
          seed_accent: seeds.accent,
          seed_secondary: seeds.secondary,
          surface_lightness: surfaceLightness,
        }),
      });
      if (!res.ok) {
        setError(extractError(await res.json(), t("errorSaveFailed")));
      } else {
        await fetchSchemes();
      }
    } catch { setError(t("errorSaveFailedBackend")); }
    finally { setSaving(false); }
  }

  async function activateScheme(scheme: SavedScheme) {
    setActivating(true); setError("");
    try {
      const res = await fetch(`${API_BASE}/api/admin/color-schemes/${scheme.id}/activate`, {
        method: "PATCH",
      });
      if (!res.ok) {
        setError(t("errorActivationFailed"));
      } else {
        await fetchSchemes();
        refreshTheme();
        loadScheme(scheme);
      }
    } catch { setError(t("errorActivationFailed")); }
    finally { setActivating(false); }
  }

  const seedColorRows = [
    { key: "primary" as const, label: t("labelPrimary"), desc: t("descPrimary") },
    { key: "accent" as const,  label: t("labelAccent"),  desc: t("descAccent")  },
    { key: "secondary" as const, label: t("labelSecondary"), desc: t("descSecondary") },
  ];

  return (
    <div className="w-[340px] flex-shrink-0 flex flex-col gap-4">

      {/* Saved Schemes */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
          {t("sectionSavedSchemes")}
        </div>
        <div className="flex gap-2 flex-wrap">
          {schemes.map((scheme) => (
            <button
              key={scheme.id}
              onClick={() => activateScheme(scheme)}
              disabled={activating}
              className="flex flex-col items-center gap-1 disabled:opacity-60"
              title={t("titleActivate", { name: scheme.name })}
            >
              <div
                className="w-11 h-11 rounded-lg relative"
                style={{
                  background: scheme.seed_primary,
                  boxShadow: scheme.is_active
                    ? `0 0 0 2px white, 0 0 0 4px ${scheme.seed_secondary}`
                    : "0 1px 3px rgba(0,0,0,0.15)",
                }}
              >
                <div
                  className="absolute bottom-1 right-1 w-3.5 h-3.5 rounded-sm"
                  style={{ background: scheme.seed_accent }}
                />
              </div>
              <span className="text-[10px] font-semibold max-w-[44px] truncate" style={{ color: "var(--color-neutral-dark, #2C3E50)" }}>
                {scheme.name}
              </span>
              {scheme.is_active && (
                <span className="text-[9px] font-semibold" style={{ color: "var(--color-gold)" }}>{t("badgeActive")}</span>
              )}
            </button>
          ))}
          <button onClick={startNew} className="flex flex-col items-center gap-1 opacity-50 hover:opacity-80">
            <div className="w-11 h-11 rounded-lg border-2 border-dashed border-gray-300 flex items-center justify-center text-gray-400 text-xl">
              {t("iconNewScheme")}
            </div>
            <span className="text-[10px] text-gray-400">{t("labelNew")}</span>
          </button>
        </div>
      </div>

      {/* Seed Colors */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
          {t("sectionSeedColors")}
        </div>
        <div className="flex flex-col gap-3">
          {seedColorRows.map(({ key, label, desc }) => (
            <div key={key} className="flex items-center gap-3">
              <label className="cursor-pointer flex-shrink-0" title={t("titlePickColor", { label })}>
                <div
                  className="w-9 h-9 rounded-lg border border-gray-200"
                  style={{ background: seeds[key] }}
                />
                <input
                  type="color"
                  value={seeds[key]}
                  onChange={(e) => setSeeds((prev) => ({ ...prev, [key]: e.target.value }))}
                  className="sr-only"
                />
              </label>
              <div className="flex-1 min-w-0">
                <div className="text-xs font-semibold" style={{ color: "var(--color-neutral-dark, #2C3E50)" }}>{label}</div>
                <div className="text-[11px] text-gray-400">{desc}</div>
              </div>
              <code className="text-[11px] text-gray-500 bg-gray-50 px-1.5 py-0.5 rounded flex-shrink-0">
                {seeds[key]}
              </code>
            </div>
          ))}
        </div>
      </div>

      {/* Surface Tint */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
          {t("sectionSurfaceTint")}
        </div>
        <div className="text-[11px] text-gray-400 mb-3">
          {t("descSurfaceTint")}
        </div>
        <input
          type="range"
          min={88}
          max={99}
          step={1}
          value={Math.round(surfaceLightness * 100)}
          onChange={(e) => setSurfaceLightness(parseInt(e.target.value) / 100)}
          className="w-full"
          style={{ accentColor: "var(--color-primary)" }}
        />
        <div className="flex justify-between items-center mt-1">
          <span className="text-[10px] text-gray-400">{t("labelPrimary")}</span>
          <code className="text-xs font-semibold" style={{ color: "var(--color-neutral-dark, #2C3E50)" }}>
            {t("surfaceLightnessValue", { value: Math.round(surfaceLightness * 100) })}
          </code>
          <span className="text-[10px] text-gray-400">{t("labelWhite")}</span>
        </div>
      </div>

      {/* Save Scheme */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
          {t("sectionSaveScheme")}
        </div>
        {error && (
          <p className="text-xs mb-2" style={{ color: "var(--color-critical, #e53e3e)" }}>{error}</p>
        )}
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("namePlaceholder")}
          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm mb-3 focus:outline-none"
          style={{ color: "var(--color-neutral-dark, #2C3E50)" }}
        />
        <div className="flex gap-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 text-white rounded-lg py-2 text-xs font-semibold disabled:opacity-50"
            style={{ background: "var(--color-primary)" }}
          >
            {saving ? t("buttonSaving") : t("buttonSave")}
          </button>
          <button
            onClick={async () => {
              if (!name.trim()) { setError(t("errorNameRequired")); return; }
              setSaving(true); setError("");
              try {
                const res = await fetch(`${API_BASE}/api/admin/color-schemes`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    name: name.trim(),
                    seed_primary: seeds.primary,
                    seed_accent: seeds.accent,
                    seed_secondary: seeds.secondary,
                    surface_lightness: surfaceLightness,
                  }),
                });
                if (!res.ok) {
                  setError(extractError(await res.json(), t("errorSaveFailed")));
                } else {
                  const saved: SavedScheme = await res.json();
                  await activateScheme(saved);
                }
              } catch { setError(t("errorSaveFailedBackend")); }
              finally { setSaving(false); }
            }}
            disabled={saving || activating}
            className="flex-1 text-white rounded-lg py-2 text-xs font-semibold disabled:opacity-50"
            style={{ background: "var(--color-teal)" }}
          >
            {saving || activating ? t("buttonSaving") : t("buttonSaveActivate")}
          </button>
        </div>
      </div>
    </div>
  );
}
