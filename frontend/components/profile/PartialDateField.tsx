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

// US290 (H1.2/H1.9/H1.12) — a Monat select (optional, "—" for unknown) + a
// Jahr input. Emits the canonical `YYYY-MM`/`YYYY`/`null` shapes via
// lib/partialDate.ts. A legacy value the picker cannot parse (e.g.
// "Q3 2019") is shown verbatim on an "Originalwert" line and left alone
// (never overwritten) unless the user actually picks a Monat/Jahr.

import { useTranslations } from "next-intl";
import { formatPartialDate, parsePartialDate } from "@/lib/partialDate";

const MONTHS = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, "0"));

interface PartialDateFieldProps {
  id: string;
  label: string;
  value: string | null;
  onChange: (value: string | null) => void;
  disabled?: boolean;
}

export function PartialDateField({ id, label, value, onChange, disabled }: PartialDateFieldProps) {
  const t = useTranslations("profile");
  const parsed = parsePartialDate(value);
  const isLegacy = parsed === "legacy";
  const year = parsed && parsed !== "legacy" ? String(parsed.year) : "";
  const month = parsed && parsed !== "legacy" && parsed.month !== null ? String(parsed.month).padStart(2, "0") : "";

  function emit(nextYear: string, nextMonth: string) {
    const trimmedYear = nextYear.trim();
    if (!/^\d{4}$/.test(trimmedYear)) {
      // No 4-digit year -> nothing the picker can represent; the canonical
      // "unknown" value is null, NEVER an empty string (H1.9).
      onChange(null);
      return;
    }
    const yearNum = Number(trimmedYear);
    if (nextMonth === "") {
      onChange(formatPartialDate({ year: yearNum, month: null }));
      return;
    }
    onChange(formatPartialDate({ year: yearNum, month: Number(nextMonth) }));
  }

  return (
    <div className="space-y-1">
      <p id={`${id}-label`} className="text-xs font-medium text-on-surface-variant">
        {label}
      </p>
      {isLegacy && (
        <p className="text-xs text-on-surface-variant" data-testid={`${id}-legacy-original`}>
          {t("entryEditor.dateOriginalValue", { value: value ?? "" })}
        </p>
      )}
      <div className="flex gap-2">
        <select
          id={`${id}-month`}
          data-testid={`${id}-month`}
          aria-label={t("entryEditor.dateMonthAria", { label })}
          value={month}
          disabled={disabled}
          onChange={(e) => emit(year, e.target.value)}
          className="rounded-lg border border-outline-variant bg-white px-2 py-1.5 text-sm text-on-surface"
        >
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative "unknown month" placeholder */}
          <option value="">{"—"}</option>
          {MONTHS.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <input
          id={`${id}-year`}
          data-testid={`${id}-year`}
          type="text"
          inputMode="numeric"
          placeholder={t("entryEditor.dateYearPlaceholder")}
          aria-label={t("entryEditor.dateYearAria", { label })}
          value={year}
          disabled={disabled}
          onChange={(e) => emit(e.target.value, month)}
          className="w-24 rounded-lg border border-outline-variant bg-white px-2 py-1.5 text-sm text-on-surface"
        />
      </div>
    </div>
  );
}
