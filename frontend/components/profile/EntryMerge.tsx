"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// Hazelnut build 5 (U-11) — merge two entries of the same profile section.
// The user drags one entry onto another (or picks a target in a dialog); a
// preview shows what the merged entry looks like and asks only the open
// questions (differing dates, possible duplicate bullets). Nothing is saved
// until "Merge"; afterwards a banner offers one-click undo.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { useLocale, useTranslations } from "next-intl";
import { GripVertical } from "lucide-react";
import {
  buildChoices,
  commitMerge,
  formatPair,
  previewComplete,
  previewMerge,
  undoLastMerge,
  type DuplicateOutcome,
  type EntryRef,
  type MergePreview,
  type MergeSection,
} from "@/lib/profile-merge";
import { ReviewApiError } from "@/lib/profile-review";
import { formatDisplayDate, type DisplayLocale } from "@/lib/profileDisplay";

interface EntryMergeContextValue {
  begin: (source: EntryRef, target?: EntryRef) => void;
  dragSource: EntryRef | null;
  setDragSource: (ref: EntryRef | null) => void;
  refsFor: (section: MergeSection) => EntryRef[];
}

const EntryMergeContext = createContext<EntryMergeContextValue | null>(null);

/** The merge context, or null when no provider is mounted (editors then render no merge controls). */
export function useEntryMerge(): EntryMergeContextValue | null {
  return useContext(EntryMergeContext);
}

interface DialogState {
  source: EntryRef;
  target: EntryRef | null;
}

interface BannerState {
  source: EntryRef;
  target: EntryRef;
  undone: boolean;
  failed: boolean;
}

type Answers = Record<string, { outcome: DuplicateOutcome; merged_text?: string }>;

export function EntryMergeProvider({
  entriesBySection,
  onProfileChanged,
  children,
}: {
  entriesBySection: Partial<Record<MergeSection, EntryRef[]>>;
  onProfileChanged: () => void;
  children: ReactNode;
}) {
  const t = useTranslations("profile.entryMerge");
  const [dragSource, setDragSource] = useState<EntryRef | null>(null);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [banner, setBanner] = useState<BannerState | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);

  const begin = useCallback((source: EntryRef, target?: EntryRef) => {
    setDragSource(null);
    setBanner(null);
    setDialog({ source, target: target ?? null });
  }, []);

  const refsFor = useCallback(
    (section: MergeSection) => entriesBySection[section] ?? [],
    [entriesBySection],
  );

  const value = useMemo<EntryMergeContextValue>(
    () => ({ begin, dragSource, setDragSource, refsFor }),
    [begin, dragSource, refsFor],
  );

  async function handleUndo() {
    if (!banner || undoBusy) return;
    setUndoBusy(true);
    try {
      await undoLastMerge();
      setBanner({ ...banner, undone: true, failed: false });
      onProfileChanged();
    } catch {
      setBanner({ ...banner, failed: true });
    } finally {
      setUndoBusy(false);
    }
  }

  return (
    <EntryMergeContext.Provider value={value}>
      {children}
      {dialog &&
        createPortal(
          <MergeDialog
            key={`${dialog.source.id}:${dialog.target?.id ?? ""}`}
            source={dialog.source}
            initialTarget={dialog.target}
            candidates={refsFor(dialog.source.section).filter((r) => r.id !== dialog.source.id)}
            onClose={() => setDialog(null)}
            onDone={(source, target) => {
              setDialog(null);
              setBanner({ source, target, undone: false, failed: false });
              onProfileChanged();
            }}
          />,
          document.body,
        )}
      {banner &&
        createPortal(
          <div
            data-testid="entry-merge-banner"
            role="status"
            className="fixed bottom-4 left-1/2 z-[70] flex -translate-x-1/2 items-center gap-3 rounded-lg bg-neutral-dark px-4 py-2.5 text-sm text-white shadow-lg"
            style={{ maxWidth: "calc(100vw - 32px)" }}
          >
            <span>
              {banner.failed
                ? t("failed")
                : banner.undone
                  ? t("undone")
                  : t("done", { source: banner.source.label, target: banner.target.label })}
            </span>
            {!banner.undone && (
              <button
                type="button"
                data-testid="entry-merge-undo"
                disabled={undoBusy}
                onClick={() => void handleUndo()}
                className="font-bold underline disabled:opacity-50"
              >
                {t("undo")}
              </button>
            )}
            <button
              type="button"
              data-testid="entry-merge-dismiss"
              onClick={() => setBanner(null)}
              className="text-white/80 hover:text-white"
            >
              {t("dismiss")}
            </button>
          </div>,
          document.body,
        )}
    </EntryMergeContext.Provider>
  );
}

/** Per-entry controls: drag handle (md+) and a "Merge…" button. */
export function EntryMergeControls({ entry }: { entry: EntryRef }) {
  const t = useTranslations("profile.entryMerge");
  const ctx = useEntryMerge();
  if (!ctx || !entry.id) return null;
  if (ctx.refsFor(entry.section).filter((r) => r.id).length < 2) return null;

  function onDragStart(e: DragEvent<HTMLSpanElement>) {
    e.dataTransfer?.setData("text/plain", entry.id);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
    ctx?.setDragSource(entry);
  }

  return (
    <>
      <span
        role="img"
        draggable
        data-testid="entry-merge-handle"
        aria-label={t("dragHandleAria", { entry: entry.label })}
        onDragStart={onDragStart}
        onDragEnd={() => ctx.setDragSource(null)}
        className="hidden cursor-grab text-on-surface-variant md:inline-flex"
      >
        <GripVertical className="h-4 w-4" aria-hidden="true" />
      </span>
      <button
        type="button"
        data-testid="entry-merge-open"
        aria-label={t("menuItem")}
        title={t("menuItem")}
        onClick={() => ctx.begin(entry)}
        className="text-xs font-medium text-primary hover:underline"
      >
        {t("button")}
      </button>
    </>
  );
}

/** Drag-and-drop wiring for an entry's wrapper element. */
export function useEntryDropTarget(entry: EntryRef | null) {
  const ctx = useEntryMerge();
  const [over, setOver] = useState(false);
  const source = ctx?.dragSource ?? null;
  const valid = Boolean(
    ctx && entry && entry.id && source && source.section === entry.section && source.id !== entry.id,
  );

  const dropProps = {
    onDragOver(e: DragEvent<HTMLElement>) {
      if (!valid) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
      setOver(true);
    },
    onDragLeave(e: DragEvent<HTMLElement>) {
      const next = e.relatedTarget as Node | null;
      if (next && e.currentTarget.contains(next)) return;
      setOver(false);
    },
    onDrop(e: DragEvent<HTMLElement>) {
      if (!valid || !ctx || !entry || !source) return;
      e.preventDefault();
      setOver(false);
      ctx.begin(source, entry);
    },
  };

  return { dropProps, isDropTarget: valid && over };
}

/** Wrapper for one entry: applies the drop target behaviour and the drop hint. */
export function EntryDropZone({
  entry,
  className,
  children,
}: {
  entry: EntryRef | null;
  className: string;
  children: ReactNode;
}) {
  const t = useTranslations("profile.entryMerge");
  const { dropProps, isDropTarget } = useEntryDropTarget(entry);
  return (
    <div
      {...dropProps}
      className={
        isDropTarget ? "rounded-lg border-2 border-dashed border-teal bg-teal-container pl-3" : className
      }
    >
      {isDropTarget && entry && (
        <p data-testid="entry-merge-drop-hint" className="text-xs font-medium text-teal">
          {t("dropHint", { entry: entry.label })}
        </p>
      )}
      {children}
    </div>
  );
}

const OUTCOMES: Array<{ outcome: DuplicateOutcome; key: "keepFirst" | "keepSecond" | "keepBoth" | "combine"; testid: string }> = [
  { outcome: "keep_current", key: "keepFirst", testid: "entry-merge-dup-keep_current" },
  { outcome: "take_new", key: "keepSecond", testid: "entry-merge-dup-take_new" },
  { outcome: "keep_both", key: "keepBoth", testid: "entry-merge-dup-keep_both" },
  { outcome: "merge", key: "combine", testid: "entry-merge-dup-merge" },
];

function aliasText(alias: Record<string, string | null | undefined>): string {
  return Object.values(alias)
    .filter((v): v is string => typeof v === "string" && v.trim().length > 0)
    .join(" · ");
}

function MergeDialog({
  source,
  initialTarget,
  candidates,
  onClose,
  onDone,
}: {
  source: EntryRef;
  initialTarget: EntryRef | null;
  candidates: EntryRef[];
  onClose: () => void;
  onDone: (source: EntryRef, target: EntryRef) => void;
}) {
  const t = useTranslations("profile.entryMerge");
  const tProfile = useTranslations("profile");
  const locale: DisplayLocale = useLocale() === "de" ? "de" : "en";
  const [target, setTarget] = useState<EntryRef | null>(initialTarget);
  const [picked, setPicked] = useState<EntryRef | null>(null);
  const [preview, setPreview] = useState<MergePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [dates, setDates] = useState<"target" | "source" | null>(null);
  const [answers, setAnswers] = useState<Answers>({});
  const [committing, setCommitting] = useState(false);
  const [stale, setStale] = useState(false);
  const [failed, setFailed] = useState(false);
  const targetId = target?.id ?? null;
  const reqSeq = useRef(0);

  const loadPreview = useCallback(
    async (tid: string) => {
      const seq = ++reqSeq.current;
      setLoading(true);
      setFailed(false);
      try {
        const p = await previewMerge(source.section, tid, source.id);
        if (seq !== reqSeq.current) return;
        setPreview(p);
        setDates(null);
        setAnswers({});
      } catch {
        if (seq !== reqSeq.current) return;
        setPreview(null);
        setFailed(true);
      } finally {
        if (seq === reqSeq.current) setLoading(false);
      }
    },
    [source.section, source.id],
  );

  useEffect(() => {
    // Fetching the preview when the target is chosen is the effect's job.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (targetId) void loadPreview(targetId);
  }, [targetId, loadPreview]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function confirm() {
    if (!target || !preview || committing) return;
    setCommitting(true);
    setFailed(false);
    setStale(false);
    try {
      await commitMerge(source.section, target.id, source.id, buildChoices(preview, dates, answers));
      onDone(source, target);
    } catch (err) {
      if (err instanceof ReviewApiError && err.status === 409) {
        setStale(true);
        await loadPreview(target.id);
      } else {
        setFailed(true);
      }
    } finally {
      setCommitting(false);
    }
  }

  const complete = preview ? previewComplete(preview, dates, answers) : false;
  const fmt = (d: string) => formatDisplayDate(d, locale);
  const present = tProfile("present");
  const btnGhost =
    "rounded-lg border border-outline-variant px-4 py-2 text-[13px] font-bold text-on-surface hover:bg-surface-container disabled:opacity-50";
  const btnPrimary =
    "rounded-lg bg-primary px-4 py-2 text-[13px] font-bold text-white hover:opacity-90 disabled:opacity-50";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={target ? t("previewTitle") : t("pickTitle")}
      data-testid="entry-merge-dialog"
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4"
    >
      <div className="max-h-[92vh] w-full max-w-[640px] overflow-y-auto rounded-xl bg-white p-6 shadow-xl">
        {!target ? (
          <>
            <h3 className="mb-2 text-base font-bold text-on-surface">{t("pickTitle")}</h3>
            <p className="mb-4 text-sm text-on-surface-variant">{t("pickLead", { entry: source.label })}</p>
            {candidates.length === 0 ? (
              <p className="text-sm text-on-surface-variant">{t("pickNone")}</p>
            ) : (
              <ul className="space-y-2">
                {candidates.map((c) => {
                  const isPicked = picked?.id === c.id;
                  return (
                    <li key={c.id}>
                      <button
                        type="button"
                        data-testid="entry-merge-pick-option"
                        aria-pressed={isPicked}
                        onClick={() => setPicked(c)}
                        className={`w-full rounded-lg px-3 py-2 text-left text-sm ${
                          isPicked
                            ? "border-2 border-teal bg-teal-container"
                            : "border border-outline-variant bg-white hover:bg-surface-container"
                        }`}
                      >
                        <span className="block font-medium text-on-surface">{c.label}</span>
                        <span className="block text-xs text-on-surface-variant">
                          {[c.period, c.period && c.period === source.period ? t("sameDates") : "", isPicked ? t("selected") : ""]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            <div className="mt-5 flex items-center justify-end gap-2">
              <button type="button" data-testid="entry-merge-cancel" onClick={onClose} className={btnGhost}>
                {t("cancel")}
              </button>
              <button
                type="button"
                data-testid="entry-merge-pick-next"
                disabled={!picked}
                onClick={() => picked && setTarget(picked)}
                className={btnPrimary}
              >
                {t("pickNext")}
              </button>
            </div>
          </>
        ) : (
          <>
            <h3 className="mb-2 text-base font-bold text-on-surface">{t("previewTitle")}</h3>
            <p className="mb-4 text-sm text-on-surface-variant">{t("previewLead")}</p>
            {loading && <p className="text-sm text-on-surface-variant">{t("previewLoading")}</p>}
            {stale && (
              <p
                data-testid="entry-merge-stale"
                className="mb-3 rounded-lg border border-warning/40 bg-warning-container px-3 py-2 text-sm text-on-surface"
              >
                {t("stale")}
              </p>
            )}
            {failed && (
              <p data-testid="entry-merge-failed" className="mb-3 text-sm text-critical">
                {t("failed")}
              </p>
            )}
            {preview && !loading && (
              <div className="space-y-4">
                <div className="rounded-lg border border-outline-variant bg-surface-container px-3 py-2 text-sm">
                  <p className="font-semibold text-on-surface">{target.label}</p>
                  <p className="text-xs text-on-surface-variant">{t("keepsEntry", { source: source.label })}</p>
                </div>

                {preview.aliases.some((a) => aliasText(a)) && (
                  <div>
                    <p className="text-xs font-medium text-on-surface-variant">{t("aliasesLabel")}</p>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {preview.aliases.map((a, i) =>
                        aliasText(a) ? (
                          <span
                            key={i}
                            className="rounded-full bg-teal-container px-2.5 py-0.5 text-xs text-on-surface"
                          >
                            {aliasText(a)}
                          </span>
                        ) : null,
                      )}
                    </div>
                    <p className="mt-1 text-xs text-on-surface-variant">{t("aliasesHint")}</p>
                  </div>
                )}

                {preview.date_conflict && (
                  <fieldset>
                    <legend className="mb-1 text-sm font-medium text-on-surface">{t("datesQuestion")}</legend>
                    {(["target", "source"] as const).map((which) => {
                      const owner = which === "target" ? target : source;
                      const selected = dates === which;
                      return (
                        <label
                          key={which}
                          className={`mb-2 flex cursor-pointer items-start gap-2 rounded-lg px-3 py-2 text-sm ${
                            selected ? "border-2 border-teal bg-teal-container" : "border border-outline-variant"
                          }`}
                        >
                          <input
                            type="radio"
                            name="entry-merge-dates"
                            data-testid={`entry-merge-dates-${which}`}
                            checked={selected}
                            onChange={() => setDates(which)}
                            className="mt-1"
                          />
                          <span>
                            <span className="block text-on-surface">
                              {formatPair(preview.date_conflict![which], fmt, present)}
                            </span>
                            <span className="block text-xs text-on-surface-variant">{owner.label}</span>
                          </span>
                        </label>
                      );
                    })}
                  </fieldset>
                )}

                {preview.duplicate_pairs.length > 0 && (
                  <div>
                    <p className="mb-2 text-sm font-medium text-on-surface">
                      {t("duplicatesTitle", { count: preview.duplicate_pairs.length })}
                    </p>
                    <div className="space-y-3">
                      {preview.duplicate_pairs.map((p) => {
                        const answer = answers[p.pair_id];
                        return (
                          <div
                            key={p.pair_id}
                            data-testid="entry-merge-dup-card"
                            className="rounded-lg border border-outline-variant p-3 text-sm"
                          >
                            <p className="text-xs text-on-surface-variant">{target.label}</p>
                            <p className="mb-2 text-on-surface">{p.target_text}</p>
                            <p className="text-xs text-on-surface-variant">{source.label}</p>
                            <p className="mb-2 text-on-surface">{p.source_text}</p>
                            <div className="grid grid-cols-2 gap-2">
                              {OUTCOMES.map((o) => {
                                if (o.outcome === "merge" && p.proposal === null) return null;
                                const on = answer?.outcome === o.outcome;
                                return (
                                  <button
                                    key={o.outcome}
                                    type="button"
                                    data-testid={o.testid}
                                    aria-pressed={on}
                                    onClick={() =>
                                      setAnswers((prev) => ({
                                        ...prev,
                                        [p.pair_id]: {
                                          ...prev[p.pair_id],
                                          outcome: o.outcome,
                                          ...(o.outcome === "merge" && prev[p.pair_id]?.merged_text === undefined
                                            ? { merged_text: p.proposal ?? "" }
                                            : {}),
                                        },
                                      }))
                                    }
                                    className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                                      on
                                        ? "border-2 border-teal bg-teal-container text-on-surface"
                                        : "border border-outline-variant bg-white text-on-surface hover:bg-surface-container"
                                    }`}
                                  >
                                    {t(o.key)}
                                  </button>
                                );
                              })}
                            </div>
                            {p.proposal === null && (
                              <p className="mt-2 text-xs text-on-surface-variant">{t("combineUnavailable")}</p>
                            )}
                            {answer?.outcome === "merge" && (
                              <label className="mt-2 block text-xs font-medium text-on-surface-variant">
                                {t("combinedTextLabel")}
                                <textarea
                                  data-testid="entry-merge-dup-text"
                                  value={answer.merged_text ?? ""}
                                  onChange={(e) =>
                                    setAnswers((prev) => ({
                                      ...prev,
                                      [p.pair_id]: { outcome: "merge", merged_text: e.target.value },
                                    }))
                                  }
                                  rows={3}
                                  className="mt-1 w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-sm text-on-surface"
                                />
                              </label>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {preview.unique_bullets.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-on-surface-variant">
                      {t("bulletsLabel", { count: preview.unique_bullets.length })}
                    </p>
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-sm text-on-surface">
                      {preview.unique_bullets.map((b, i) => (
                        <li key={i}>{b}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <p className="text-xs text-on-surface-variant">{t("nothingLost")}</p>
              </div>
            )}
            <div className="mt-5 flex items-start justify-end gap-2">
              <button type="button" data-testid="entry-merge-cancel" onClick={onClose} className={btnGhost}>
                {t("cancel")}
              </button>
              <div className="flex flex-col items-end">
                <button
                  type="button"
                  data-testid="entry-merge-confirm"
                  disabled={!preview || loading || !complete || committing}
                  onClick={() => void confirm()}
                  className={btnPrimary}
                >
                  {committing ? t("committing") : t("confirm")}
                </button>
                {preview && !loading && !complete && (
                  <span className="mt-1 text-xs text-on-surface-variant">{t("answerFirst")}</span>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
