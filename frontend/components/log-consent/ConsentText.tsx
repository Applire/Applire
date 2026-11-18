"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The full consent text to the test system's logs (board B4), in a dialog.
 * Every value it names (operator, contact, three recipients, the age limit) is
 * an instance setting; an unset one prints the catalog's "not set" fallback.
 */

import { useLocale, useTranslations } from "next-intl";

import { ActionButton, Dialog } from "@/components/account/Dialog";
import { consentParams, footerDate, type LogConsentText } from "@/lib/log-consent";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-4">
      <h3 className="mb-1 text-[14px] font-bold text-on-surface">{title}</h3>
      {children}
    </section>
  );
}

export function ConsentTextBody({ text }: { text: LogConsentText }) {
  const t = useTranslations("logConsent");
  const locale = useLocale();
  const p = consentParams(text, t("notSet"));
  return (
    <div className="space-y-1 leading-relaxed" data-testid="log-consent-text">
      <p>{t("text.intro")}</p>
      <Section title={t("text.storeTitle")}>
        <ul className="list-disc space-y-0.5 pl-5">
          <li>{t("text.store1")}</li>
          <li>{t("text.store2")}</li>
          <li>{t("text.store3")}</li>
          <li>{t("text.store4")}</li>
          <li>{t("text.store5")}</li>
        </ul>
        <p className="mt-1.5">{t("text.storeNote")}</p>
      </Section>
      <Section title={t("text.purposeTitle")}>
        <p>{t("text.purposeBody")}</p>
      </Section>
      <Section title={t("text.readersTitle")}>
        <ul className="list-disc space-y-0.5 pl-5">
          <li>{t("text.readerOperator", p)}</li>
          <li>{t("text.readerAssistants", p)}</li>
          <li>{t("text.readerHost", p)}</li>
        </ul>
        <p className="mt-1.5">{t("text.modelNote", p)}</p>
      </Section>
      <Section title={t("text.durationTitle")}>
        <p>{t("text.durationBody", p)}</p>
      </Section>
      <Section title={t("text.withdrawTitle")}>
        <p>{t("text.withdrawBody", p)}</p>
      </Section>
      <Section title={t("text.voluntaryTitle")}>
        <p>{t("text.voluntaryBody")}</p>
      </Section>
      <p className="mt-4 border-t border-outline-variant pt-3 text-[12px]">
        {t("text.footer", { ...p, date: footerDate(text.text_date, locale), version: text.text_version })}
      </p>
    </div>
  );
}

export function ConsentTextDialog({
  open,
  onClose,
  text,
}: {
  open: boolean;
  onClose: () => void;
  text: LogConsentText;
}) {
  const t = useTranslations("logConsent");
  return (
    <Dialog
      open={open}
      onClose={onClose}
      wide
      title={t("text.title")}
      testId="log-consent-text-dialog"
      actions={
        <ActionButton data-testid="log-consent-text-close" onClick={onClose}>
          {t("close")}
        </ActionButton>
      }
    >
      <ConsentTextBody text={text} />
    </Dialog>
  );
}

/** The link that opens the full text (boards B1, B2). */
export function ConsentTextLink({ onClick }: { onClick: () => void }) {
  const t = useTranslations("logConsent");
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid="log-consent-text-link"
      className="font-semibold text-teal underline underline-offset-2 hover:text-teal/80"
    >
      {t("whatLink")}
    </button>
  );
}

/** The consent checkbox (B1, B2): never pre-ticked. */
export function ConsentCheckbox({
  checked,
  onChange,
  text,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  text: LogConsentText;
}) {
  const t = useTranslations("logConsent");
  const p = consentParams(text, t("notSet"));
  return (
    <label className="my-3 flex cursor-pointer items-start gap-2.5 text-[13.5px] leading-snug text-on-surface">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        data-testid="log-consent-checkbox"
        className="mt-0.5 h-[18px] w-[18px] flex-shrink-0 accent-teal"
      />
      <span>{t("checkbox", p)}</span>
    </label>
  );
}
