// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * Q3-4 / Q8-3 (Sorbet wave 2b) — the line under a bullet an interview answer
 * replaced: "Ersetzt: „<alt>“ [Rückgängig] [Ausblenden]". Undo puts the old
 * bullet back BESIDE the new one; dismiss hides the line (the new bullet stays,
 * the old wording stays in the edit history). Both go through the committer.
 */
import { useState } from "react";
import { useTranslations } from "next-intl";
import { dismissBulletReplace, undoBulletReplace } from "@/lib/profile-health";
import type { ProfileSectionsResponse, ReplacedBullet } from "@/lib/profile-entries";

interface ReplacedBulletLineProps {
  entryId: string;
  record: ReplacedBullet;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}

export function ReplacedBulletLine({ entryId, record, onProfileUpdated }: ReplacedBulletLineProps) {
  const t = useTranslations("profile.replacedBullet");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function act(kind: "undo" | "dismiss") {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    const call = kind === "undo" ? undoBulletReplace : dismissBulletReplace;
    const result = await call(entryId, record.text);
    setBusy(false);
    if (result) onProfileUpdated(result);
    else setFailed(true);
  }

  return (
    <span data-testid="replaced-bullet-line" className="mt-0.5 block text-xs text-on-surface-variant">
      <span>{t("label", { old: record.replaced })}</span>
      <button
        type="button"
        data-testid="replaced-bullet-undo"
        disabled={busy}
        onClick={() => act("undo")}
        className="ml-2 font-medium text-primary hover:underline disabled:opacity-50"
      >
        {t("undo")}
      </button>
      <button
        type="button"
        data-testid="replaced-bullet-dismiss"
        disabled={busy}
        onClick={() => act("dismiss")}
        className="ml-2 font-medium text-primary hover:underline disabled:opacity-50"
      >
        {t("dismiss")}
      </button>
      {failed && (
        <span role="alert" className="ml-2 text-critical">
          {t("failed")}
        </span>
      )}
    </span>
  );
}
