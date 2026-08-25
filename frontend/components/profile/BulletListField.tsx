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

// US290 (H1.7) — a reusable string[] field: add / remove / edit inline, plus
// an optional move up/down (bullet order is content — used for
// responsibilities/achievements; entry order itself is never editable here,
// generation sorts entries by date). Removing a bullet needs no confirmation
// (only removing a whole ENTRY does — that lives in the entry editors).

import { useTranslations } from "next-intl";

interface BulletListFieldProps {
  id: string;
  label: string;
  items: string[];
  onChange: (items: string[]) => void;
  addButtonLabel: string;
  /** Per-item aria-label, e.g. (i) => `Erfolg ${i + 1} bearbeiten`. */
  itemAriaLabel: (index: number) => string;
  disabled?: boolean;
  allowReorder?: boolean;
}

export function BulletListField({
  id,
  label,
  items,
  onChange,
  addButtonLabel,
  itemAriaLabel,
  disabled,
  allowReorder = true,
}: BulletListFieldProps) {
  const t = useTranslations("profile");

  function updateItem(index: number, value: string) {
    const next = [...items];
    next[index] = value;
    onChange(next);
  }

  function removeItem(index: number) {
    onChange(items.filter((_, i) => i !== index));
  }

  function addItem() {
    onChange([...items, ""]);
  }

  function moveItem(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= items.length) return;
    const next = [...items];
    const [moved] = next.splice(index, 1);
    next.splice(target, 0, moved);
    onChange(next);
  }

  return (
    <div className="space-y-1.5" data-testid={id}>
      <p className="text-xs font-medium text-on-surface-variant">{label}</p>
      <ul className="space-y-1.5">
        {items.map((item, i) => (
          <li key={i} className="flex items-start gap-1.5">
            <textarea
              data-testid={`${id}-item-${i}`}
              aria-label={itemAriaLabel(i)}
              value={item}
              disabled={disabled}
              onChange={(e) => updateItem(i, e.target.value)}
              rows={1}
              className="min-w-0 flex-1 resize-y rounded-lg border border-outline-variant bg-white px-2 py-1.5 text-sm text-on-surface"
            />
            <div className="flex shrink-0 flex-col gap-0.5">
              {allowReorder && (
                <>
                  <button
                    type="button"
                    data-testid={`${id}-up-${i}`}
                    aria-label={t("entryEditor.bulletMoveUp")}
                    disabled={disabled || i === 0}
                    onClick={() => moveItem(i, -1)}
                    className="rounded border border-outline-variant px-1.5 text-xs text-on-surface-variant hover:bg-surface-container disabled:opacity-30"
                  >
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative move-up glyph, aria-label carries the meaning */}
                    {"↑"}
                  </button>
                  <button
                    type="button"
                    data-testid={`${id}-down-${i}`}
                    aria-label={t("entryEditor.bulletMoveDown")}
                    disabled={disabled || i === items.length - 1}
                    onClick={() => moveItem(i, 1)}
                    className="rounded border border-outline-variant px-1.5 text-xs text-on-surface-variant hover:bg-surface-container disabled:opacity-30"
                  >
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative move-down glyph, aria-label carries the meaning */}
                    {"↓"}
                  </button>
                </>
              )}
              <button
                type="button"
                data-testid={`${id}-remove-${i}`}
                aria-label={t("entryEditor.bulletRemove")}
                disabled={disabled}
                onClick={() => removeItem(i)}
                className="rounded border border-outline-variant px-1.5 text-xs text-critical hover:bg-critical/10 disabled:opacity-30"
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative remove glyph, aria-label carries the meaning */}
                {"✕"}
              </button>
            </div>
          </li>
        ))}
      </ul>
      <button
        type="button"
        data-testid={`${id}-add`}
        disabled={disabled}
        onClick={addItem}
        className="text-xs font-medium text-primary hover:underline disabled:opacity-50"
      >
        {addButtonLabel}
      </button>
    </div>
  );
}
