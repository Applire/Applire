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
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { AppTopbar } from "@/components/shell/AppTopbar";
import {
  MergeGateDialog,
  type MergeGate,
  type ResolveAction,
  type StagedResolveResult,
} from "@/components/profile/MergeGateDialog";

// Open gate states still require the user to merge or discard; resolved_* are inert.
const OPEN_GATES: ReadonlySet<string> = new Set(["not_a_cv", "name_divergence"]);

// Empty string default lets Next.js rewrites handle /api/* routing in all environments
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface UploadHistoryItem {
  id: string;
  original_filename: string;
  mime_type: string;
  byte_size: number;
  created_at: string;
  completeness_score: number | null;
  gate_status?: string | null;
  staged_name?: string | null;
}

interface GateInfo {
  gate: MergeGate;
  stagedId: string;
  accountName?: string | null;
  cvName?: string | null;
}

interface ProfileImportViewProps {
  flowId?: string;
}

async function readApiError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
      return detail.map((e: { msg?: string }) => e.msg ?? JSON.stringify(e)).join("; ");
  } catch {
    // ignore parse error
  }
  return res.statusText || `HTTP ${res.status}`;
}

export function ProfileImportView({ flowId }: ProfileImportViewProps) {
  const t = useTranslations("profileImport");
  const tp = useTranslations("profileUpdate");
  const tg = useTranslations("mergeGate");
  const router = useRouter();

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [flowError, setFlowError] = useState("");
  const [completenessScore, setCompletenessScore] = useState<number | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState(false);
  const [history, setHistory] = useState<UploadHistoryItem[]>([]);
  const [gateInfo, setGateInfo] = useState<GateInfo | null>(null);

  const mainInputRef = useRef<HTMLInputElement>(null);
  const linkedinInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/profile/uploads`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setHistory)
      .catch(() => {});
  }, []);

  function refreshHistory() {
    fetch(`${API_BASE}/api/profile/uploads`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setHistory)
      .catch(() => {});
  }

  async function handleFile(file: File) {
    setError("");
    setFlowError("");
    setUploadSuccess(false);
    setCompletenessScore(null);
    setLoading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const isZip = file.name.toLowerCase().endsWith(".zip");
      const endpoint = isZip ? "/api/profile/import" : "/api/profile/upload";

      const res = await fetch(`${API_BASE}${endpoint}`, { method: "POST", body: form });
      if (!res.ok) throw new Error(await readApiError(res));

      const data = await res.json();

      // US167: the pre-merge gate held the merge. Park nothing happened to the
      // profile — open the resolve dialog instead of treating it as a success.
      if (data.status === "GATED") {
        setGateInfo({
          gate: data.gate as MergeGate,
          stagedId: data.staged_id,
          accountName: data.account_name,
          cvName: data.cv_name,
        });
        refreshHistory();
        return;
      }

      await proceedAfterUpdate(data.completeness_score ?? null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("uploadErrorGeneric"));
    } finally {
      setLoading(false);
    }
  }

  /** Shared post-merge path: surface the success strip, refresh history, advance. */
  async function proceedAfterUpdate(completeness: number | null) {
    setCompletenessScore(completeness);
    setUploadSuccess(true);
    refreshHistory();

    if (flowId) {
      try {
        await proceedToGaps(flowId);
      } catch (fe: unknown) {
        setFlowError(fe instanceof Error ? fe.message : t("flowErrorGeneric"));
      }
    } else {
      router.push("/profile");
    }
  }

  async function handleGateResolved(action: ResolveAction, _data: StagedResolveResult) {
    setGateInfo(null);
    if (action === "merge") {
      await proceedAfterUpdate(_data.completeness_score ?? null);
    } else {
      refreshHistory();
    }
  }

  function reviewParkedUpload(item: UploadHistoryItem) {
    if (!item.gate_status || !OPEN_GATES.has(item.gate_status)) return;
    setGateInfo({
      gate: item.gate_status as MergeGate,
      stagedId: item.id,
      cvName: item.staged_name,
    });
  }

  async function proceedToGaps(fId: string) {
    const stateRes = await fetch(`${API_BASE}/api/flow/${fId}/state`);
    if (!stateRes.ok) throw new Error(t("flowNotFound"));
    const { job_id } = await stateRes.json();

    const gapRes = await fetch(`${API_BASE}/api/job/${job_id}/gaps`, { method: "POST" });
    if (!gapRes.ok) {
      const msg = await readApiError(gapRes);
      throw new Error(gapRes.status === 504 ? `KI-Zeitüberschreitung: ${msg}` : msg);
    }
    const gapData = await gapRes.json();

    const advRes = await fetch(`${API_BASE}/api/flow/${fId}/advance`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ step: "gap_analysis", artifact_id: gapData.id }),
    });
    if (!advRes.ok) throw new Error(await readApiError(advRes));
    router.push(`/flow/${fId}/gaps`);
  }

  return (
    <>
      <AppTopbar
        mode="detail"
        backHref="/profile"
        backLabelKey="shell.profile"
        pageTitle={tp("importTitle")}
      />
    <div className="p-8 max-w-[1100px] mx-auto">
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
        {/* ── Left column ── */}
        <div>
          <input
            ref={mainInputRef}
            data-testid="main-file-input"
            type="file"
            accept=".pdf,.docx,.doc,.zip"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleFile(file);
              e.target.value = "";
            }}
          />
          <input
            ref={linkedinInputRef}
            data-testid="linkedin-file-input"
            type="file"
            accept=".zip,.pdf"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleFile(file);
              e.target.value = "";
            }}
          />

          {/* Glass dropzone */}
          <div
            className={cn(
              "bg-white/90 backdrop-blur-md border-2 border-dashed border-outline-variant rounded-xl",
              "p-10 flex flex-col items-center justify-center text-center cursor-pointer",
              "shadow-[0_0_40px_-10px_rgba(0,51,153,0.12)] transition-all duration-200",
              "hover:border-primary hover:border-solid",
              loading && "opacity-60 pointer-events-none"
            )}
            onClick={() => mainInputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const file = e.dataTransfer.files[0];
              if (file) handleFile(file);
            }}
          >
            <div className="w-[72px] h-[72px] rounded-full bg-gradient-to-br from-primary to-primary-container flex items-center justify-center mb-4 shadow-[0_8px_24px_rgba(0,51,153,0.25)]">
              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
              <span aria-hidden="true" className="material-symbols-outlined text-white text-[36px]" style={{ fontVariationSettings: "'FILL' 1" }}>cloud_upload</span>
            </div>
            <h3 className="font-manrope text-[18px] font-bold text-primary mb-1.5">
              {loading ? t("uploading") : t("dropTitle")}
            </h3>
            <p className="text-sm text-on-surface-variant mb-5 leading-relaxed max-w-xs">
              {t("subtitle")}
            </p>
            <button
              type="button"
              data-testid="main-upload-button"
              className="bg-primary-container text-primary px-6 py-2 rounded-full text-sm font-bold hover:bg-primary-fixed-dim transition-colors"
              onClick={(e) => { e.stopPropagation(); mainInputRef.current?.click(); }}
              disabled={loading}
            >
              {t("browse")}
            </button>
            <p className="text-[11px] text-outline-variant mt-3">{t("formats")}</p>
          </div>

          {/* LinkedIn secondary card */}
          <button
            type="button"
            className="w-full mt-4 bg-white border border-outline-variant rounded-xl p-3.5 flex items-center gap-4 hover:shadow-md transition-all hover:-translate-y-px text-left disabled:opacity-60"
            onClick={() => linkedinInputRef.current?.click()}
            disabled={loading}
          >
            <div className="w-11 h-11 rounded-[10px] bg-[#0077b5]/10 flex items-center justify-center flex-shrink-0">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="#0077b5" aria-hidden="true">
                <path d="M19 3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14m-.5 15.5v-5.3a3.26 3.26 0 0 0-3.26-3.26c-.85 0-1.84.52-2.32 1.3v-1.11h-2.79v8.37h2.79v-4.93c0-.77.62-1.4 1.39-1.4a1.4 1.4 0 0 1 1.4 1.4v4.93h2.79M6.88 8.56a1.68 1.68 0 0 0 1.68-1.68c0-.93-.75-1.69-1.68-1.69a1.69 1.69 0 0 0-1.69 1.69c0 .93.76 1.68 1.69 1.68m1.39 9.94v-8.37H5.5v8.37h2.77z" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[13px] font-bold text-primary">{t("linkedinCardTitle")}</p>
              <p className="text-[11px] text-on-surface-variant mt-0.5 leading-snug">{t("linkedinCardDesc")}</p>
            </div>
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
            <span aria-hidden="true" className="material-symbols-outlined text-outline-variant text-[20px]">chevron_right</span>
          </button>

          {/* Success strip */}
          {uploadSuccess && !error && (
            <div
              data-testid="upload-success-strip"
              className="mt-3 flex items-center gap-2.5 bg-[#dcfce7] border border-[#86efac] rounded-[10px] px-4 py-3"
            >
              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
              <span aria-hidden="true" className="material-symbols-outlined text-[#16a34a] text-[20px]" style={{ fontVariationSettings: "'FILL' 1" }}>check_circle</span>
              <span className="text-[13px] font-semibold text-[#166534]">
                {completenessScore !== null
                  ? t("successWithScore", { score: Math.round(completenessScore * 100) })
                  : t("successNoScore")}
              </span>
            </div>
          )}

          {/* Upload error strip */}
          {error && (
            <div className="mt-3 flex items-center gap-2.5 bg-red-50 border border-red-200 rounded-[10px] px-4 py-3">
              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
              <span aria-hidden="true" className="material-symbols-outlined text-red-500 text-[20px]" style={{ fontVariationSettings: "'FILL' 1" }}>error</span>
              <span className="text-[13px] font-semibold text-red-700">{error}</span>
            </div>
          )}

          {/* Flow navigation error strip — shown alongside success strip */}
          {flowError && (
            <div className="mt-3 flex items-center gap-2.5 bg-amber-50 border border-amber-200 rounded-[10px] px-4 py-3">
              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
              <span aria-hidden="true" className="material-symbols-outlined text-amber-500 text-[20px]" style={{ fontVariationSettings: "'FILL' 1" }}>warning</span>
              <span className="text-[13px] font-semibold text-amber-700">{flowError}</span>
            </div>
          )}
        </div>

        {/* ── Right column ── */}
        <div className="flex flex-col gap-5">
          {/* Upload history */}
          <div
            data-testid="upload-history-panel"
            className="bg-white/90 backdrop-blur-md border border-outline-variant rounded-xl p-[18px] shadow-[0_0_40px_-10px_rgba(0,51,153,0.1)]"
          >
            <div className="flex items-center justify-between mb-3.5">
              <span className="font-manrope text-[15px] font-bold text-primary">{t("historyTitle")}</span>
              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
              <span aria-hidden="true" className="material-symbols-outlined text-[20px] text-on-surface-variant">history</span>
            </div>
            {history.length === 0 ? (
              <p className="text-[12px] text-outline text-center py-4">{t("historyEmpty")}</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {history.slice(0, 3).map((item) => {
                  const isLinkedIn =
                    item.mime_type === "application/zip" ||
                    item.original_filename.toLowerCase().endsWith(".zip");
                  const isOpenGate =
                    !!item.gate_status && OPEN_GATES.has(item.gate_status);
                  return (
                    <li
                      key={item.id}
                      className={cn(
                        "flex items-center gap-2.5 px-2.5 py-2 rounded-lg",
                        isOpenGate
                          ? "bg-amber-50 border border-amber-200"
                          : "bg-surface-container-low"
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          "material-symbols-outlined text-[20px]",
                          isOpenGate
                            ? "text-amber-600"
                            : isLinkedIn
                              ? "text-[#0077b5]"
                              : "text-primary"
                        )}
                        style={{ fontVariationSettings: "'FILL' 0" }}
                      >
                        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon names */}
                        {isOpenGate ? "error" : isLinkedIn ? "link" : "description"}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-[12px] font-semibold text-primary truncate">
                          {item.original_filename}
                        </p>
                        {isOpenGate ? (
                          <p className="text-[10px] font-semibold text-amber-700 mt-0.5">
                            {tg("badge")}
                          </p>
                        ) : (
                          <p className="text-[10px] text-on-surface-variant mt-0.5">
                            {new Date(item.created_at).toLocaleDateString()}
                            {item.completeness_score !== null && (
                              <>
                                {" · "}{Math.round(item.completeness_score * 100)}{" %"}
                              </>
                            )}
                          </p>
                        )}
                      </div>
                      {isOpenGate ? (
                        <button
                          type="button"
                          data-testid="gate-review-btn"
                          onClick={() => reviewParkedUpload(item)}
                          className="flex-shrink-0 text-[11px] font-bold text-amber-700 px-2 py-1 rounded-md hover:bg-amber-100"
                        >
                          {tg("reviewAction")}
                        </button>
                      ) : (
                        /* Decorative only — no action */
                        <span className="material-symbols-outlined text-[16px] text-outline-variant" aria-hidden="true">
                          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon names */}
                          {isLinkedIn ? "refresh" : "download"}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            <button type="button" disabled className="w-full mt-3 py-1.5 text-[12px] font-bold text-primary bg-transparent border-none cursor-pointer hover:underline disabled:cursor-not-allowed disabled:opacity-50">
              {t("viewAll")}
            </button>
          </div>

          {/* AI context card */}
          <div className="rounded-xl p-[18px] bg-primary text-white relative overflow-hidden">
            <svg
              className="absolute inset-0 w-full h-full opacity-10 pointer-events-none"
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <path d="M0 100 Q 25 0 50 100 T 100 100" fill="none" stroke="white" strokeWidth="0.5" />
            </svg>
            <div className="relative z-10">
              <div className="flex items-center gap-1.5 mb-2">
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                <span aria-hidden="true" className="material-symbols-outlined text-[18px] text-secondary-container" style={{ fontVariationSettings: "'FILL' 1" }}>auto_awesome</span>
                <span className="text-[10px] font-semibold tracking-widest uppercase text-primary-fixed-dim">
                  {t("whyTag")}
                </span>
              </div>
              <h4 className="font-manrope text-[15px] font-bold mb-1.5">{t("whyTitle")}</h4>
              <p className="text-[12px] leading-relaxed opacity-85">{t("whyBody")}</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    {gateInfo && (
      <MergeGateDialog
        gate={gateInfo.gate}
        stagedId={gateInfo.stagedId}
        accountName={gateInfo.accountName}
        cvName={gateInfo.cvName}
        onResolved={handleGateResolved}
        onCancel={() => setGateInfo(null)}
      />
    )}
    </>
  );
}
