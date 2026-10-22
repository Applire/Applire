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


import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

type CLTone = "formal" | "professional" | "conversational";

interface GenerateCoverLetterModalProps {
  jobId: string;
  prefillRecipientName?: string | null;
  prefillRecipientCompany?: string | null;
  /** If provided, modal shows "Regenerate" header and pre-fills fields */
  existingInputs?: {
    recipient_name?: string | null;
    recipient_company?: string | null;
    salary?: string | null;
    availability?: string | null;
    motivation?: string | null;
    tone?: CLTone;
  } | null;
  onClose: () => void;
  /** The second argument carries what was submitted, so the page can tell the
   * preview which optional inputs the letter was generated without. */
  onGenerated: (coverLetterId: string, inputs?: SubmittedInputs) => void;
}

export interface SubmittedInputs {
  recipient_name: string | null;
  recipient_company: string | null;
  salary: string | null;
  availability: string | null;
  motivation: string | null;
  tone: CLTone;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export function GenerateCoverLetterModal({
  jobId,
  prefillRecipientName,
  prefillRecipientCompany,
  existingInputs,
  onClose,
  onGenerated,
}: GenerateCoverLetterModalProps) {
  const t = useTranslations("coverLetter");
  const tc = useTranslations("common");
  const isRegenerate = existingInputs != null;
  const [recipientName, setRecipientName] = useState(
    existingInputs?.recipient_name ?? prefillRecipientName ?? ""
  );
  const [recipientCompany, setRecipientCompany] = useState(
    existingInputs?.recipient_company ?? prefillRecipientCompany ?? ""
  );
  const [salary, setSalary] = useState(existingInputs?.salary ?? "");
  const [availability, setAvailability] = useState(existingInputs?.availability ?? "");
  const [availabilityFromProfile, setAvailabilityFromProfile] = useState(false);
  const availabilityTouched = useRef(false);
  const [motivation, setMotivation] = useState(existingInputs?.motivation ?? "");
  const [tone, setTone] = useState<CLTone>(existingInputs?.tone ?? "formal");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Frontend collector #604 — the recipient-company field is labelled
  // "(aus Stellenanzeige)" and shipped empty even though the analysis already
  // knew the employer. A confidence gap rather than a functional one: the
  // backend re-derives the company independently, so the letter came out right
  // while the form looked ignorant of the target.
  //
  // `prefillRecipientCompany` already existed as a prop and NO caller passed it
  // — not the CV page, not either of the cover-letter page's two mounts. The
  // lookup lives here rather than at the three call sites so one fix covers all
  // three, and because `GET /api/job/{id}` is the endpoint that actually carries
  // the field: the flow state's `JobAnalysisSummary` has only `job_id` and
  // `role_title`, so a page-level fix would have had to add this same request.
  //
  // Fill only what is still empty (an explicit prop, a stored regenerate value
  // and anything the user has typed all win), and fail silently — a missing
  // company must never block generating a letter.
  useEffect(() => {
    if (existingInputs?.recipient_company || prefillRecipientCompany) return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/job/${jobId}`);
        if (!res.ok) return;
        const data = (await res.json()) as { company_name?: string | null };
        const company = data.company_name?.trim();
        if (!cancelled && company) setRecipientCompany((current) => current || company);
      } catch {
        /* the field simply stays empty — it is optional */
      }
    })();
    return () => {
      cancelled = true;
    };
    // Runs once per opened modal; the identity inputs above cannot change while
    // it is mounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // 787:L3 — the notice period is a fact about the person, so a value stored in
  // the profile pre-fills the field, but only when this application has none of
  // its own. It stays editable and per application; nothing is written back.
  // A failed read leaves the form exactly as it was.
  useEffect(() => {
    if (existingInputs?.availability) return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/profile`);
        if (!res.ok) return;
        const data = (await res.json()) as {
          profile?: { availability?: { text?: string | null } | null } | null;
        };
        const text = data?.profile?.availability?.text?.trim();
        // Only into a still-empty field: anything the user typed meanwhile wins.
        if (!cancelled && text && !availabilityTouched.current) {
          setAvailability(text);
          setAvailabilityFromProfile(true);
        }
      } catch {
        /* the field simply stays empty */
      }
    })();
    return () => {
      cancelled = true;
    };
    // Once per opened modal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const TONE_OPTIONS: { value: CLTone; label: string; sub: string }[] = [
    { value: "formal", label: t("toneFormalLabel"), sub: t("toneFormalSub") },
    { value: "professional", label: t("toneProfessionalLabel"), sub: t("toneProfessionalSub") },
    { value: "conversational", label: t("toneConversationalLabel"), sub: t("toneConversationalSub") },
  ];

  async function handleGenerate() {
    setLoading(true);
    setError(null);
    try {
      const inputs: SubmittedInputs = {
        recipient_name: recipientName || null,
        recipient_company: recipientCompany || null,
        salary: salary || null,
        availability: availability || null,
        motivation: motivation || null,
        tone,
      };
      const res = await fetch(`${API_BASE}/api/cover-letter/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, ...inputs }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        // #311: a hard-coded English default here always won over the
        // translated fallback below (a thrown Error is always an Error, so
        // err.message was never empty). An empty message defers to t().
        // A 5xx answers a structured body (error_code/message/error_id), not a
        // string: only a string detail is shown, anything else defers to t().
        const detail = (data as { detail?: unknown }).detail;
        throw new Error(typeof detail === "string" && detail.trim() ? detail : "");
      }
      const data = await res.json();
      onGenerated((data as { cover_letter_id: string }).cover_letter_id, inputs);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "";
      setError(message || t("errorGenerationFailed"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      data-testid="cover-letter-modal"
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 p-6 flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-bold">
            {isRegenerate ? t("regenerate") : t("generate")}
          </h2>
          <p className="text-xs text-neutral-500 mt-1">
            {t("modalIntro")}
          </p>
        </div>

        {/* Recipient */}
        <div>
          <label className="flex items-center gap-1 text-xs font-semibold text-neutral-600 uppercase tracking-wide mb-1">
            {t("recipientLabel")}
            <span className="font-normal text-neutral-400 normal-case">{t("recipientHint")}</span>
          </label>
          <div className="flex gap-2">
            <input
              className="flex-1 border border-neutral-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder={t("recipientNamePlaceholder")}
              value={recipientName}
              onChange={(e) => setRecipientName(e.target.value)}
              data-testid="cl-recipient-name"
            />
            <input
              className="flex-1 border border-neutral-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder={t("recipientCompanyPlaceholder")}
              value={recipientCompany}
              onChange={(e) => setRecipientCompany(e.target.value)}
              data-testid="cl-recipient-company"
            />
          </div>
        </div>

        {/* Availability */}
        <div>
          <label className="flex items-center gap-1 text-xs font-semibold text-neutral-600 uppercase tracking-wide mb-1">
            {t("availabilityLabel")}
            <span className="font-normal text-neutral-400 normal-case">{t("optionalHint")}</span>
          </label>
          <input
            className="w-full border border-neutral-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder={t("availabilityPlaceholder")}
            value={availability}
            onChange={(e) => {
              availabilityTouched.current = true;
              setAvailability(e.target.value);
              setAvailabilityFromProfile(false);
            }}
            data-testid="cl-availability"
          />
          {availabilityFromProfile && (
            <p className="mt-1 text-xs text-neutral-500" data-testid="cl-availability-profile-hint">
              {t("availabilityFromProfile")}
            </p>
          )}
        </div>

        {/* Motivation */}
        <div>
          <label className="flex items-center gap-1 text-xs font-semibold text-neutral-600 uppercase tracking-wide mb-1">
            {t("motivationLabel")}
            <span className="font-normal text-neutral-400 normal-case">{t("optionalHint")}</span>
          </label>
          <textarea
            className="w-full border border-neutral-300 rounded px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-500"
            rows={3}
            placeholder={t("motivationPlaceholder")}
            value={motivation}
            onChange={(e) => setMotivation(e.target.value)}
            data-testid="cl-motivation"
          />
        </div>

        {/* Salary */}
        <div>
          <label className="flex items-center gap-1 text-xs font-semibold text-neutral-600 uppercase tracking-wide mb-1">
            {t("salaryLabel")}
            <span className="font-normal text-neutral-400 normal-case">{t("optionalHint")}</span>
          </label>
          <input
            className="w-full border border-neutral-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder={t("salaryPlaceholder")}
            value={salary}
            onChange={(e) => setSalary(e.target.value)}
            data-testid="cl-salary"
          />
        </div>

        {/* Tone */}
        <div>
          <label className="block text-xs font-semibold text-neutral-600 uppercase tracking-wide mb-2">
            {t("toneLabel")}
          </label>
          <div className="flex gap-2">
            {TONE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setTone(opt.value)}
                className={`flex-1 rounded border p-2 text-left transition-colors ${
                  tone === opt.value
                    ? "border-blue-500 bg-blue-50"
                    : "border-neutral-200 hover:border-neutral-400"
                }`}
                data-testid={`cl-tone-${opt.value}`}
              >
                <div className="text-xs font-semibold">{opt.label}</div>
                <div className="text-xs text-neutral-500">{opt.sub}</div>
              </button>
            ))}
          </div>
        </div>

        {error && (
          <p className="text-xs text-red-600 bg-red-50 rounded p-2">{error}</p>
        )}

        <div className="flex gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="flex-1 border border-neutral-300 rounded py-2.5 text-sm hover:border-neutral-500 transition-colors disabled:opacity-50"
            data-testid="cl-modal-cancel"
          >
            {tc("cancel")}
          </button>
          <button
            type="button"
            onClick={handleGenerate}
            disabled={loading}
            className="flex-[2] bg-blue-600 text-white rounded py-2.5 text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
            data-testid="cl-modal-generate"
          >
            {loading ? t("generating") : t("generateArrow")}
          </button>
        </div>
      </div>
    </div>
  );
}
