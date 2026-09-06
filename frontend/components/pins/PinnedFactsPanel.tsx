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

// E056/ADR-077 — fact pins: the user's seat at the budget table. A pin is a
// verbatim quote from the candidate's own Master Profile, addressed by entry
// type + entry id, that MUST appear in the CV and/or letter (hierarchy:
// truth > pin > budget). This panel lists the application's current pins and
// drives POST/DELETE /api/applications/{id}/pins via a picker that only ever
// offers the entry's OWN content fields as the quote — never free text
// (clause 1: a pin carries no free text of its own).
//
// The 2026-09 UX pass (COPY.md, mock/PanelAfter + Main) changed what the
// control SAYS, not what it does: the header states the promise instead of
// naming the mechanism, the count moves out of the title, target and fate
// merge into one chip per document, and the gaps page meets the feature as a
// `teaser` card before generation (ADR-077 cl. 6 keeps the control in the
// gap/coverage view; ADR-081 cl. 3 keeps it outside the finding groups — no
// finding row links a pin as its remedy).

import { useCallback, useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown } from "lucide-react";
import { useTranslations } from "next-intl";
import type { PinnedFactReportEntry } from "@/components/cv/ATSChecksPanel";
import { FirstUseExplainer } from "@/components/explainers/FirstUseExplainer";
import { useExplainer } from "@/components/explainers/useExplainer";

export type FactPinEntryType =
  | "work"
  | "project"
  | "volunteer"
  | "signature_story"
  | "skill"
  | "certification"
  | "education"
  | "language"
  | "publication";

export interface FactPin {
  pin_id: string;
  entry_type: FactPinEntryType;
  entry_id: string;
  quote: string;
  targets: ("cv" | "letter")[];
  stale: boolean;
}

/** The first-use explainer this control opens behind (#679, D-7 allowlist). */
export const FACT_PINS_EXPLAINER_ID = "fact_pins_intro";

// Mirrors backend/applire/constants.py MAX_FACT_PINS (ADR-077 clause 6).
const MAX_FACT_PINS = 10;

// #580: the backend refuses (HTTP 422) a `cv` target on these entry types —
// the CV template renders neither section. Gate client-side so the picker
// never offers a target the API will reject.
const CV_TARGET_UNAVAILABLE_TYPES: FactPinEntryType[] = ["volunteer", "publication"];

// Mirrors backend/applire/services/fact_pins.py _SECTIONS — the profile address
// book: entry_type -> (MasterProfileData list attr, the entry's own content
// fields a quote may be picked from). Order here also drives the picker's
// group order.
const ENTRY_TYPES: FactPinEntryType[] = [
  "work",
  "project",
  "volunteer",
  "signature_story",
  "skill",
  "certification",
  "education",
  "language",
  "publication",
];

const SECTION_KEY: Record<FactPinEntryType, string> = {
  work: "work_experience",
  project: "projects",
  volunteer: "volunteer_activities",
  signature_story: "signature_stories",
  skill: "skills",
  certification: "certifications",
  education: "education",
  language: "languages",
  publication: "publications",
};

const CONTENT_FIELDS: Record<FactPinEntryType, string[]> = {
  work: ["responsibilities", "achievements"],
  project: ["responsibilities", "achievements"],
  volunteer: ["responsibilities", "achievements"],
  signature_story: ["challenge", "mechanism", "outcome", "benchmark"],
  skill: ["name"],
  certification: ["name"],
  education: ["institution", "degree", "field", "thesis_title"],
  language: ["language", "level"],
  publication: ["title"],
};

const LABEL_FIELDS: Record<FactPinEntryType, string[]> = {
  work: ["role", "company"],
  project: ["name"],
  volunteer: ["role", "organization"],
  signature_story: ["title"],
  skill: ["name"],
  certification: ["name"],
  education: ["degree", "institution"],
  language: ["language", "level"],
  publication: ["title"],
};

// COPY.md §A: the entry label sits UNDER the quote to say where the sentence
// comes from. For these four types the label IS the quote (a skill's name, a
// certification's name, …), so repeating it would be noise.
const LABEL_LINE_SUPPRESSED: FactPinEntryType[] = [
  "skill",
  "certification",
  "language",
  "publication",
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type ProfileEntry = Record<string, any>;

interface EntryOption {
  entry_type: FactPinEntryType;
  id: string;
  label: string;
  quotes: string[];
}

function stringField(entry: ProfileEntry, field: string): string[] {
  const v = entry[field];
  if (Array.isArray(v)) {
    return v.filter((x): x is string => typeof x === "string" && x.trim().length > 0);
  }
  if (typeof v === "string" && v.trim().length > 0) return [v];
  return [];
}

// Only entries the pin-add endpoint could ever accept: a status field set to
// "unconfirmed"/"denied" (ADR-061 clause 3) is never claimable — pinning it
// would launder it past the claim gate, which sits ABOVE pins (ADR-077
// clause 2). Types without a status field are always claimable.
function isClaimable(entry: ProfileEntry): boolean {
  return entry.status !== "unconfirmed" && entry.status !== "denied";
}

// One label rule, two readers: the picker's entry list and the row's "where
// this sentence comes from" line. They must never disagree.
function entryLabel(
  entry: ProfileEntry,
  type: FactPinEntryType,
  fallback: () => string,
): string {
  return (
    LABEL_FIELDS[type]
      .map((f) => (typeof entry[f] === "string" ? entry[f].trim() : ""))
      .filter((s) => s.length > 0)
      .join(" · ") || fallback()
  );
}

function buildEntryOptions(
  profile: ProfileEntry | undefined,
  fallbackLabel: (type: FactPinEntryType, index: number) => string,
): Record<FactPinEntryType, EntryOption[]> {
  const result = {} as Record<FactPinEntryType, EntryOption[]>;
  for (const type of ENTRY_TYPES) {
    const list: ProfileEntry[] = (profile?.[SECTION_KEY[type]] as ProfileEntry[]) ?? [];
    const options: EntryOption[] = [];
    list.forEach((entry, index) => {
      if (!entry || typeof entry.id !== "string" || !isClaimable(entry)) return;
      const quotes = Array.from(
        new Set(CONTENT_FIELDS[type].flatMap((f) => stringField(entry, f))),
      );
      if (quotes.length === 0) return;
      options.push({
        entry_type: type,
        id: entry.id,
        label: entryLabel(entry, type, () => fallbackLabel(type, index)),
        quotes,
      });
    });
    result[type] = options;
  }
  return result;
}

// The row's label line resolves against the WHOLE profile, not the picker's
// filtered option set: a pinned entry may since have become unclaimable or had
// its quote fields emptied, and the pin (and its provenance) still stands.
function buildEntryLabels(
  profile: ProfileEntry | undefined,
): Record<string, string> {
  const labels: Record<string, string> = {};
  if (!profile) return labels;
  for (const type of ENTRY_TYPES) {
    const list: ProfileEntry[] = (profile[SECTION_KEY[type]] as ProfileEntry[]) ?? [];
    for (const entry of list) {
      if (!entry || typeof entry.id !== "string") continue;
      const label = entryLabel(entry, type, () => "");
      if (label) labels[`${type}:${entry.id}`] = label;
    }
  }
  return labels;
}

// Insertion order (ADR-077: pins are additive-only) grouped by entry_type —
// group order follows the first appearance of that type in the pin list.
function groupPins(pins: FactPin[]): { entry_type: FactPinEntryType; pins: FactPin[] }[] {
  const order: FactPinEntryType[] = [];
  const byType = new Map<FactPinEntryType, FactPin[]>();
  for (const pin of pins) {
    if (!byType.has(pin.entry_type)) {
      byType.set(pin.entry_type, []);
      order.push(pin.entry_type);
    }
    byType.get(pin.entry_type)!.push(pin);
  }
  return order.map((entry_type) => ({ entry_type, pins: byType.get(entry_type)! }));
}

async function apiErrorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body.detail === "string" ? body.detail : res.statusText || `HTTP ${res.status}`;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

// Finding #1 (adversarial pass, 2026-09-06): add_fact_pin's duplicate-pin
// ValueError (backend/applire/services/fact_pins.py:210) is mapped to 422 by
// the router (applire/routers/application.py add_pin) — the SAME status the
// cap, claim-gate and quote-resolution errors use, so a status check alone
// cannot tell them apart. Match this exact backend detail text and localise
// it; every other 422 keeps today's raw passthrough.
const DUPLICATE_PIN_DETAIL = "This fact is already pinned on this application.";

// #580 — per-document fate marker. Fetches ONE document's ATS report and
// indexes its pinned_facts by pin_id. Tolerant by design: a 404/failed fetch,
// a null report (document pending/failed), or a null/absent pinned_facts
// array (audited without pin context) all collapse to an empty map — every
// per-pin lookup against an empty map naturally reads as "not measured yet"
// (see fateStatus below), so the marker never lies by inventing a fate.
async function fetchPinnedFactsMap(url: string): Promise<Map<string, PinnedFactReportEntry>> {
  const map = new Map<string, PinnedFactReportEntry>();
  try {
    const res = await fetch(url);
    if (!res.ok) return map;
    const data = await res.json();
    const list = data?.report?.pinned_facts;
    if (!Array.isArray(list)) return map;
    for (const entry of list as PinnedFactReportEntry[]) {
      if (entry && typeof entry.pin_id === "string") map.set(entry.pin_id, entry);
    }
    return map;
  } catch {
    return map;
  }
}

type FateStatus = "present" | "unmet" | "truthFloor" | "notMeasured";

function fateStatus(
  lookup: Map<string, PinnedFactReportEntry>,
  pinId: string,
): { status: FateStatus; ledgerConflict: string[] } {
  const entry = lookup.get(pinId);
  if (!entry) return { status: "notMeasured", ledgerConflict: [] };
  if (entry.present) return { status: "present", ledgerConflict: [] };
  // removed_by_truth_floor takes precedence over the plain unmet text
  // (hierarchy: truth > pin — the floor's reason for absence is the one
  // that matters, never dressed up as a garden-variety miss).
  if (entry.removed_by_truth_floor) return { status: "truthFloor", ledgerConflict: [] };
  return { status: "unmet", ledgerConflict: entry.ledger_conflict ?? [] };
}

const FATE_CHIP_CLASS: Record<FateStatus, string> = {
  present: "bg-success-container text-success",
  unmet: "bg-critical-container text-critical",
  truthFloor: "bg-critical-container text-critical",
  notMeasured: "bg-surface-container text-on-surface-variant",
};

const NO_DOCUMENT_CHIP_CLASS = "bg-surface-container text-on-surface-variant";

function fateText(
  fate: { status: FateStatus; ledgerConflict: string[] },
  t: ReturnType<typeof useTranslations>,
): string {
  switch (fate.status) {
    case "present":
      return t("pins.fate.present");
    case "truthFloor":
      return t("pins.fate.removedByTruthFloor");
    case "notMeasured":
      return t("pins.fate.notMeasured");
    case "unmet": {
      const base = t("pins.fate.unmet");
      if (fate.ledgerConflict.length === 0) return base;
      return `${base} ${t("pins.fate.ledgerConflict", { terms: fate.ledgerConflict.join(", ") })}`;
    }
  }
}

// COPY.md §A: ONE chip per target, carrying the target AND what became of the
// pin there. With no document yet there is nothing to report, so the chip says
// only where the pin is meant to land — "not measured yet" is for a document
// that EXISTS but has no usable report, never a stand-in for "no document".
function PinTargetChip({
  target,
  pinId,
  lookup,
  hasDocument,
  t,
}: {
  target: "cv" | "letter";
  pinId: string;
  lookup: Map<string, PinnedFactReportEntry>;
  hasDocument: boolean;
  t: ReturnType<typeof useTranslations>;
}) {
  const targetLabel = target === "cv" ? t("pins.targetCv") : t("pins.targetLetter");
  const fate = hasDocument ? fateStatus(lookup, pinId) : null;
  return (
    <span
      data-testid={
        fate ? `pinned-fact-fate-${target}-${pinId}` : `pinned-fact-target-${target}-${pinId}`
      }
      className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
        fate ? FATE_CHIP_CLASS[fate.status] : NO_DOCUMENT_CHIP_CLASS
      }`}
    >
      {fate ? t("pins.chip", { target: targetLabel, fate: fateText(fate, t) }) : targetLabel}
    </span>
  );
}

export type PinnedFactsPanelVariant = "panel" | "teaser";

export function PinnedFactsPanel({
  applicationId,
  apiBase,
  cvId = null,
  coverLetterId = null,
  variant = "panel",
}: {
  applicationId: string;
  apiBase: string;
  // #580: when present, the panel measures each pin's fate against that
  // document's ATS report. Absent/null means no such document exists yet —
  // never rendered as "not measured", just nothing to say.
  cvId?: string | null;
  coverLetterId?: string | null;
  // `teaser` is the gaps-page card BEFORE generation (COPY.md §B): the same
  // panel, met as an offer rather than as a list. `panel` is the CV page's
  // Edit tab.
  variant?: PinnedFactsPanelVariant;
}) {
  const t = useTranslations("gaps");
  const tExplainers = useTranslations("explainers");
  const howBodyId = useId();

  const [pins, setPins] = useState<FactPin[] | null>(null);
  const [loadError, setLoadError] = useState(false);

  const [cvPinnedFacts, setCvPinnedFacts] = useState<Map<string, PinnedFactReportEntry>>(
    new Map(),
  );
  const [letterPinnedFacts, setLetterPinnedFacts] = useState<Map<string, PinnedFactReportEntry>>(
    new Map(),
  );

  const [howOpen, setHowOpen] = useState(false);
  const [teaserExpanded, setTeaserExpanded] = useState(false);
  const [explainerOpen, setExplainerOpen] = useState(false);
  const { shouldShow: explainerShouldShow, dismiss: dismissExplainer } =
    useExplainer(FACT_PINS_EXPLAINER_ID);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [profile, setProfile] = useState<ProfileEntry | undefined>(undefined);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState(false);
  const [selectedEntry, setSelectedEntry] = useState<EntryOption | null>(null);
  const [selectedQuote, setSelectedQuote] = useState<string | null>(null);
  const [targets, setTargets] = useState<{ cv: boolean; letter: boolean }>({
    cv: true,
    letter: true,
  });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const loadPins = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/applications/${applicationId}`);
      if (!res.ok) {
        setLoadError(true);
        return;
      }
      const data = await res.json();
      setPins(Array.isArray(data.pinned_facts) ? data.pinned_facts : []);
    } catch {
      setLoadError(true);
    }
  }, [apiBase, applicationId]);

  useEffect(() => {
    void loadPins();
  }, [loadPins]);

  // The profile is read ONCE on mount and serves two readers: the row's entry
  // label line and the picker's entry list. A failure is not fatal — the rows
  // simply drop their label line, and the picker retries on open.
  const loadProfile = useCallback(async () => {
    setProfileError(false);
    setProfileLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/profile`);
      if (!res.ok) {
        setProfileError(true);
        return;
      }
      const data = await res.json();
      setProfile(data.profile);
    } catch {
      setProfileError(true);
    } finally {
      setProfileLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    void loadProfile();
  }, [loadProfile]);

  // #580: per-document fate measurement — fetched in parallel, each
  // independently tolerant of a missing/failed/pin-less report (see
  // fetchPinnedFactsMap). No document id at all skips the fetch entirely;
  // the render layer (not this effect) decides whether that means "no chip".
  useEffect(() => {
    let cancelled = false;
    async function loadCvReport() {
      if (!cvId) {
        if (!cancelled) setCvPinnedFacts(new Map());
        return;
      }
      const map = await fetchPinnedFactsMap(`${apiBase}/api/cv/${cvId}/ats-report`);
      if (!cancelled) setCvPinnedFacts(map);
    }
    async function loadLetterReport() {
      if (!coverLetterId) {
        if (!cancelled) setLetterPinnedFacts(new Map());
        return;
      }
      const map = await fetchPinnedFactsMap(
        `${apiBase}/api/cover-letter/${coverLetterId}/ats-report`,
      );
      if (!cancelled) setLetterPinnedFacts(map);
    }
    void loadCvReport();
    void loadLetterReport();
    return () => {
      cancelled = true;
    };
  }, [apiBase, cvId, coverLetterId]);

  function fallbackLabel(type: FactPinEntryType, index: number): string {
    return `${t(`pins.entryTypes.${type}`)} ${index + 1}`;
  }

  function resetDialogSelection() {
    setSelectedEntry(null);
    setSelectedQuote(null);
    setTargets({ cv: true, letter: true });
    setSubmitError(null);
  }

  function openDialog() {
    resetDialogSelection();
    setDialogOpen(true);
    // The mount-time read normally already has it; retry only if it failed or
    // never produced a profile. The picker is never blocked on a fresh fetch.
    if (!profile && !profileLoading) void loadProfile();
  }

  // Both add paths (panel header button, teaser CTA) meet the explainer first
  // — once per user (#679), then straight into the picker.
  function startAdd() {
    if (explainerShouldShow) {
      setExplainerOpen(true);
      return;
    }
    openDialog();
  }

  function closeDialog() {
    setDialogOpen(false);
    resetDialogSelection();
  }

  async function removePin(pinId: string) {
    // Optimistic removal — idempotent DELETE, safe even if it races another tab.
    setPins((prev) => (prev ? prev.filter((p) => p.pin_id !== pinId) : prev));
    try {
      await fetch(`${apiBase}/api/applications/${applicationId}/pins/${pinId}`, {
        method: "DELETE",
      });
    } catch {
      // Best-effort: reconcile against the server on the next mount/refresh.
      void loadPins();
    }
  }

  async function confirmPin() {
    if (!selectedEntry || !selectedQuote) return;
    // #580: never send a cv target the backend would 422 on — re-derive the
    // gate here too, not just in the disabled checkbox, so a stale `targets.cv`
    // from a previously-selected entry can never leak into the POST body.
    const cvAllowedForSubmit = !CV_TARGET_UNAVAILABLE_TYPES.includes(selectedEntry.entry_type);
    const chosenTargets: ("cv" | "letter")[] = [
      ...(cvAllowedForSubmit && targets.cv ? (["cv"] as const) : []),
      ...(targets.letter ? (["letter"] as const) : []),
    ];
    if (chosenTargets.length === 0) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const res = await fetch(`${apiBase}/api/applications/${applicationId}/pins`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          entry_type: selectedEntry.entry_type,
          entry_id: selectedEntry.id,
          quote: selectedQuote,
          targets: chosenTargets,
        }),
      });
      if (!res.ok) {
        const detail = await apiErrorMessage(res);
        setSubmitError(
          res.status === 422 && detail === DUPLICATE_PIN_DETAIL
            ? t("pins.pinDuplicate")
            : detail,
        );
        return;
      }
      const created: FactPin = await res.json();
      setPins((prev) => (prev ? [...prev, created] : [created]));
      closeDialog();
    } catch {
      setSubmitError(t("pins.pinErrorGeneric"));
    } finally {
      setSubmitting(false);
    }
  }

  const pinList = pins ?? [];
  const groups = groupPins(pinList);
  const atCap = pinList.length >= MAX_FACT_PINS;
  const entryLabels = buildEntryLabels(profile);
  const entryOptions = dialogOpen ? buildEntryOptions(profile, fallbackLabel) : undefined;
  // #580: no entry selected yet → nothing to gate against, so the checkbox
  // defaults to enabled (it isn't rendered until an entry IS selected anyway).
  const cvTargetAllowed = selectedEntry
    ? !CV_TARGET_UNAVAILABLE_TYPES.includes(selectedEntry.entry_type)
    : true;
  const hasAnyTarget = (cvTargetAllowed && targets.cv) || targets.letter;
  // D-6: an entry with exactly one statement has nothing to choose between —
  // the picker shows it and moves straight on to the targets.
  const singleStatement = selectedEntry?.quotes.length === 1;

  // `capReachedTitle` interpolates {max}; it was called without it, so at the
  // cap the tooltip rendered next-intl's error fallback instead of the number.
  const addButton = (
    <button
      type="button"
      data-testid="pinned-facts-add"
      onClick={startAdd}
      disabled={atCap}
      title={atCap ? t("pins.capReachedTitle", { max: MAX_FACT_PINS }) : undefined}
      className="shrink-0 rounded-xl border border-outline-variant bg-white px-3 py-1.5 text-sm font-medium text-on-surface hover:bg-surface-container disabled:cursor-not-allowed disabled:opacity-50"
    >
      {t("pins.addButton")}
    </button>
  );

  // The panel body — shared verbatim by the CV page's `panel` variant and by
  // the gaps-page teaser once expanded (D-2: expansion is inline, in the same
  // card, never a navigation or a modal).
  const panelBody = (
    <>
      {/* Wraps rather than squeezes: in the CV page's ~400 px refinement
          sidebar the title and the add button do not fit on one line, and a
          five-word promise broken over three lines beside a button reads as a
          layout accident. Above ~560 px (the mock's width) they sit side by
          side as designed. */}
      <div className="mb-1 flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <h3
          data-testid="pinned-facts-title"
          className="min-w-[13rem] flex-1 font-heading text-lg font-bold text-on-surface"
        >
          {t("pins.title")}
        </h3>
        {addButton}
      </div>

      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        {/* COPY.md §A: the counter is a fact about a list that exists — with
            no pins yet the empty state carries the message instead. */}
        {pinList.length > 0 ? (
          <p data-testid="pinned-facts-count" className="text-xs text-on-surface-variant">
            {t("pins.counter", { count: pinList.length, max: MAX_FACT_PINS })}
          </p>
        ) : (
          !loadError && (
            <p data-testid="pinned-facts-empty" className="text-xs text-on-surface-variant">
              {t("pins.empty")}
            </p>
          )
        )}
        <button
          type="button"
          data-testid="pinned-facts-how-toggle"
          aria-expanded={howOpen}
          aria-controls={howOpen ? howBodyId : undefined}
          onClick={() => setHowOpen((v) => !v)}
          className="inline-flex shrink-0 items-center gap-0.5 text-xs text-teal hover:underline"
        >
          {t("pins.howLink")}
          <ChevronDown
            aria-hidden
            className={`h-3.5 w-3.5 transition-transform ${howOpen ? "rotate-180" : ""}`}
          />
        </button>
      </div>

      {/* D-5: byte-identical to the explainer's three paragraphs — the same
          explanation, reachable again without re-opening a dismissed card. */}
      {howOpen && (
        <div
          id={howBodyId}
          role="region"
          aria-label={t("pins.howLink")}
          data-testid="pinned-facts-how-body"
          className="mb-3 rounded-xl bg-surface-container px-3 py-2.5"
        >
          <p className="text-xs leading-relaxed text-on-surface">{t("pins.howBody")}</p>
        </div>
      )}

      {loadError && (
        <p className="text-sm text-critical" data-testid="pinned-facts-load-error">
          {t("pins.loadError")}
        </p>
      )}

      {groups.length > 0 && (
        <div className="space-y-4">
          {groups.map((group) => (
            <div key={group.entry_type}>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-on-surface-variant">
                {t(`pins.entryTypes.${group.entry_type}`)}
              </p>
              <ul className="space-y-1.5">
                {group.pins.map((pin) => {
                  const label = LABEL_LINE_SUPPRESSED.includes(pin.entry_type)
                    ? undefined
                    : entryLabels[`${pin.entry_type}:${pin.entry_id}`];
                  return (
                    <li
                      key={pin.pin_id}
                      data-testid={`pinned-fact-${pin.pin_id}`}
                      className="flex items-start justify-between gap-3 rounded-xl border border-outline-variant bg-surface-bright px-3 py-2"
                    >
                      <div className="min-w-0">
                        {/* No truncation: a pin is a promise about a whole
                            sentence, so the whole sentence is shown. */}
                        <p
                          className="text-sm text-on-surface"
                          data-testid={`pinned-fact-quote-${pin.pin_id}`}
                        >
                          {t("pins.quoteWrapped", { quote: pin.quote })}
                        </p>
                        {label && (
                          <p
                            data-testid={`pinned-fact-entry-label-${pin.pin_id}`}
                            className="mt-0.5 text-xs text-on-surface-variant"
                          >
                            {label}
                          </p>
                        )}
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          {pin.targets.includes("cv") && (
                            <PinTargetChip
                              target="cv"
                              pinId={pin.pin_id}
                              lookup={cvPinnedFacts}
                              hasDocument={Boolean(cvId)}
                              t={t}
                            />
                          )}
                          {pin.targets.includes("letter") && (
                            <PinTargetChip
                              target="letter"
                              pinId={pin.pin_id}
                              lookup={letterPinnedFacts}
                              hasDocument={Boolean(coverLetterId)}
                              t={t}
                            />
                          )}
                          {pin.stale && (
                            <span
                              data-testid={`pinned-fact-stale-${pin.pin_id}`}
                              className="rounded-full bg-warning-container px-2 py-0.5 text-[11px] font-medium text-on-surface"
                            >
                              {t("pins.stale")}
                            </span>
                          )}
                        </div>
                      </div>
                      <button
                        type="button"
                        data-testid={`pinned-fact-remove-${pin.pin_id}`}
                        aria-label={t("pins.removeAria")}
                        onClick={() => void removePin(pin.pin_id)}
                        className="shrink-0 text-xs font-medium text-on-surface-variant hover:text-critical"
                      >
                        {t("pins.remove")}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </>
  );

  const overlays = (
    <>
      {explainerOpen && (
        <FirstUseExplainer
          explainerId={FACT_PINS_EXPLAINER_ID}
          title={tExplainers("factPins.title")}
          paragraphs={[
            tExplainers("factPins.p1"),
            tExplainers("factPins.p2"),
            tExplainers("factPins.p3"),
          ]}
          continueLabel={tExplainers("factPins.continue")}
          canSuppress
          onContinue={(dontShowAgain) => {
            if (dontShowAgain) dismissExplainer();
            setExplainerOpen(false);
            openDialog();
          }}
          onCancel={() => setExplainerOpen(false)}
        />
      )}

      {/* Portal to <body> (US289 transform-trap discipline): the panel can sit
          under a transformed ancestor on this page, which would otherwise trap
          a `fixed` overlay to that ancestor's box. */}
      {dialogOpen &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("pins.dialogTitle")}
            data-testid="pinned-facts-dialog"
            className="fixed inset-0 z-[70] flex items-end md:items-center justify-center bg-black/40 p-0 md:p-4"
          >
            <div className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white p-6 shadow-xl md:max-w-lg md:rounded-xl">
              <div
                aria-hidden="true"
                className="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 md:hidden"
              />
              <h3 className={`text-base font-bold text-on-surface ${selectedEntry ? "mb-4" : ""}`}>
                {t("pins.dialogTitle")}
              </h3>

              {/* COPY.md §C: the promise sits on the entry step, where the user
                  is deciding whether to start at all; step 2 is already past it. */}
              {!selectedEntry && (
                <p
                  data-testid="pinned-facts-dialog-intro"
                  className="mb-4 mt-1 text-xs text-on-surface-variant"
                >
                  {t("pins.dialogIntro")}
                </p>
              )}

              {profileLoading && (
                <p className="text-sm text-on-surface-variant" data-testid="pinned-facts-dialog-loading">
                  {t("pins.dialogLoading")}
                </p>
              )}

              {profileError && (
                <p className="text-sm text-critical" data-testid="pinned-facts-dialog-load-error">
                  {t("pins.dialogLoadError")}
                </p>
              )}

              {!profileLoading && !profileError && !selectedEntry && entryOptions && (
                <div data-testid="pinned-facts-dialog-entries" className="space-y-4">
                  <p className="text-sm font-medium text-on-surface">
                    {t("pins.dialogChooseEntry")}
                  </p>
                  {ENTRY_TYPES.map((type) => {
                    const options = entryOptions[type];
                    if (!options || options.length === 0) return null;
                    return (
                      <div key={type}>
                        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-on-surface-variant">
                          {t(`pins.entryTypes.${type}`)}
                        </p>
                        <ul className="space-y-1">
                          {options.map((opt) => (
                            <li key={`${opt.entry_type}-${opt.id}`}>
                              <button
                                type="button"
                                data-testid={`pin-entry-${opt.entry_type}-${opt.id}`}
                                onClick={() => {
                                  setSelectedEntry(opt);
                                  // D-6: one statement means the choice is
                                  // already made — never leave the user to
                                  // "pick" from a list of one.
                                  setSelectedQuote(opt.quotes.length === 1 ? opt.quotes[0] : null);
                                }}
                                className="w-full rounded-xl border border-outline-variant px-3 py-2 text-left text-sm text-on-surface hover:bg-surface-container"
                              >
                                {opt.label}
                              </button>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })}
                  {ENTRY_TYPES.every((type) => (entryOptions[type]?.length ?? 0) === 0) && (
                    <p className="text-sm text-on-surface-variant" data-testid="pinned-facts-dialog-no-entries">
                      {t("pins.dialogNoEntries")}
                    </p>
                  )}
                </div>
              )}

              {!profileLoading && !profileError && selectedEntry && (
                <div data-testid="pinned-facts-dialog-quotes" className="space-y-4">
                  <button
                    type="button"
                    data-testid="pin-dialog-back"
                    onClick={() => {
                      setSelectedEntry(null);
                      setSelectedQuote(null);
                    }}
                    className="text-xs font-medium text-on-surface-variant hover:text-on-surface"
                  >
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative back arrow */}
                    {"← "}
                    {t("pins.dialogBack")}
                  </button>
                  {/* Same rule as the row's label line: for a skill or a
                      certification the entry label IS the statement, and
                      printing it twice above itself reads as a mistake. */}
                  {!(singleStatement && selectedEntry.label === selectedEntry.quotes[0]) && (
                    <p className="text-sm font-medium text-on-surface">{selectedEntry.label}</p>
                  )}

                  {singleStatement ? (
                    <p
                      data-testid="pinned-facts-dialog-single-quote"
                      className="rounded-xl border border-outline-variant bg-surface-container px-3 py-2 text-sm text-on-surface"
                    >
                      {selectedEntry.quotes[0]}
                    </p>
                  ) : (
                    <div>
                      <p className="mb-1.5 text-sm font-medium text-on-surface">
                        {t("pins.dialogChooseQuote")}
                      </p>
                      <ul className="space-y-1.5">
                        {selectedEntry.quotes.map((quote, i) => (
                          <li key={i}>
                            <label
                              className="flex cursor-pointer items-start gap-2 rounded-xl border border-outline-variant px-3 py-2 text-sm text-on-surface hover:bg-surface-container"
                            >
                              <input
                                type="radio"
                                name="pin-quote"
                                data-testid={`pin-quote-${i}`}
                                checked={selectedQuote === quote}
                                onChange={() => setSelectedQuote(quote)}
                                className="mt-0.5"
                              />
                              <span>{quote}</span>
                            </label>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <div>
                    <p className="mb-1.5 text-sm font-medium text-on-surface">
                      {t("pins.dialogChooseTargets")}
                    </p>
                    <div className="flex gap-4">
                      <label
                        className={`flex items-center gap-1.5 text-sm text-on-surface ${
                          cvTargetAllowed ? "" : "opacity-50"
                        }`}
                      >
                        <input
                          type="checkbox"
                          data-testid="pin-target-cv"
                          checked={cvTargetAllowed && targets.cv}
                          disabled={!cvTargetAllowed}
                          onChange={(e) =>
                            setTargets((prev) => ({ ...prev, cv: e.target.checked }))
                          }
                        />
                        {t("pins.targetCv")}
                      </label>
                      <label className="flex items-center gap-1.5 text-sm text-on-surface">
                        <input
                          type="checkbox"
                          data-testid="pin-target-letter"
                          checked={targets.letter}
                          onChange={(e) =>
                            setTargets((prev) => ({ ...prev, letter: e.target.checked }))
                          }
                        />
                        {t("pins.targetLetter")}
                      </label>
                    </div>
                    <p
                      data-testid="pin-targets-hint"
                      className="mt-1.5 text-xs text-on-surface-variant"
                    >
                      {t("pins.dialogTargetsHint")}
                    </p>
                    {/* #580: the CV renders neither `volunteer` nor
                        `publication` entries — the picker gate mirrors the
                        backend's 422 client-side, before the user ever hits it. */}
                    {!cvTargetAllowed && (
                      <p
                        data-testid="pin-target-cv-unavailable-hint"
                        className="mt-1.5 text-xs text-on-surface-variant"
                      >
                        {t("pins.dialogCvTargetUnavailable")}
                      </p>
                    )}
                  </div>

                  {submitError && (
                    <div
                      data-testid="pinned-facts-dialog-error"
                      className="rounded-xl border border-critical/40 bg-critical-container px-3 py-2"
                    >
                      <p className="text-xs font-semibold text-critical">{t("pins.pinError")}</p>
                      <p className="text-xs text-on-surface-variant">{submitError}</p>
                    </div>
                  )}
                </div>
              )}

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  data-testid="pin-dialog-cancel"
                  onClick={closeDialog}
                  disabled={submitting}
                  className="rounded-xl border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {t("pins.dialogCancel")}
                </button>
                {selectedEntry && (
                  <button
                    type="button"
                    data-testid="pin-dialog-confirm"
                    onClick={() => void confirmPin()}
                    disabled={submitting || !selectedQuote || !hasAnyTarget}
                    className="rounded-xl bg-primary px-4 py-2 text-[13px] font-bold text-white hover:opacity-90 disabled:opacity-50"
                  >
                    {submitting ? t("pins.dialogBusy") : t("pins.dialogConfirm")}
                  </button>
                )}
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );

  // COPY.md §B — the gaps page meets fact pins as an OFFER before generation:
  // empty is a question with a CTA, non-empty is a count with an inline
  // "Bearbeiten" (D-2). D-1: it stays in the scroll flow as the last card
  // above the decision-bar spacer; it never enters the fixed bottom bar.
  if (variant === "teaser") {
    return (
      <div
        data-testid="pinned-facts-teaser"
        className="mb-8 rounded-xl border border-outline-variant bg-white p-5"
      >
        {pinList.length === 0 ? (
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-on-surface-variant">
                {t("pins.teaserKicker")}
              </p>
              {/* Finding #2 (adversarial pass, 2026-09-06): this branch used to
                  fire on `pinList.length === 0` alone, so a failed pins fetch
                  (loadError, pins stays []) rendered byte-identical to an
                  honest "zero pins" — inviting a pin on top of an unknown
                  state. When the read failed, show the load error and drop
                  the CTA instead of the question/body/button. */}
              {loadError ? (
                <p className="text-sm text-critical" data-testid="pinned-facts-load-error">
                  {t("pins.loadError")}
                </p>
              ) : (
                <>
                  <h3 className="font-heading text-base font-bold text-on-surface">
                    {t("pins.teaserTitle")}
                  </h3>
                  <p className="mt-1 text-sm text-on-surface-variant">{t("pins.teaserBody")}</p>
                </>
              )}
            </div>
            {!loadError && (
              <button
                type="button"
                data-testid="pinned-facts-teaser-add"
                onClick={startAdd}
                className="shrink-0 rounded-xl border border-outline-variant bg-white px-6 py-3 text-sm font-semibold text-on-surface hover:bg-surface-container"
              >
                {t("pins.teaserButton")}
              </button>
            )}
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-on-surface-variant">
                  {t("pins.teaserKicker")}
                </p>
                <p
                  data-testid="pinned-facts-teaser-count"
                  className="font-heading text-base font-bold text-on-surface"
                >
                  {t("pins.teaserCount", { count: pinList.length })}
                </p>
              </div>
              <button
                type="button"
                data-testid="pinned-facts-teaser-edit"
                aria-expanded={teaserExpanded}
                onClick={() => setTeaserExpanded((v) => !v)}
                className="shrink-0 rounded-xl border border-outline-variant bg-white px-6 py-3 text-sm font-semibold text-on-surface hover:bg-surface-container"
              >
                {t("pins.teaserEdit")}
              </button>
            </div>
            {teaserExpanded && (
              <div
                data-testid="pinned-facts-panel"
                className="mt-4 border-t border-outline-variant pt-4"
              >
                {panelBody}
              </div>
            )}
          </>
        )}
        {overlays}
      </div>
    );
  }

  return (
    <div
      data-testid="pinned-facts-panel"
      className="mb-8 rounded-xl border border-outline-variant bg-white p-4"
    >
      {panelBody}
      {overlays}
    </div>
  );
}
