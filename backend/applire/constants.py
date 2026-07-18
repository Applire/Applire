# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

import os

# Interview orchestrator thresholds and limits (ADR 004, Iteration 14)

# Mode auto-detection: completeness_score below this → MODE B (Guided Build)
MODE_B_COMPLETENESS_THRESHOLD: float = 0.3

# Hard ceilings — session ends after this many questions even if gaps remain
INTERVIEW_HARD_CEILING_TARGETED: int = 12  # MODE A
INTERVIEW_HARD_CEILING_GUIDED: int = 20    # MODE B

# Soft targets — informational only, used for estimated_questions in response
INTERVIEW_TARGET_MIN_TARGETED: int = 3
INTERVIEW_TARGET_MIN_GUIDED: int = 5

# Per-gap question ceiling (Sprint 15): max questions asked for a single gap
# before force-advancing to the next one. Includes the initial question.
# Set INTERVIEW_MAX_QUESTIONS_PER_GAP in environment to override (e.g. in docker-compose.yml).
INTERVIEW_MAX_QUESTIONS_PER_GAP: int = int(
    os.environ.get("INTERVIEW_MAX_QUESTIONS_PER_GAP", "3")
)

# LLM review layer — retry ceiling (ADR-021, Sprint 20)
# Set LLM_REVIEW_MAX_RETRIES=0 to disable the review layer entirely.
LLM_REVIEW_MAX_RETRIES: int = int(
    os.environ.get("LLM_REVIEW_MAX_RETRIES", "2")
)

# Output budget for the reviewer's *verdict* (ADR-021 amended 2026-06-29 / US193,
# E036). The reviewer is bounded-output-by-contract: it reads the full draft + source
# (large INPUT, fine) but only ever emits a small {approved, issues, feedback} verdict
# — it must NEVER re-emit the document. So its call carries a small ceiling, far below
# the generator budget, and a capped model (e.g. mistral-medium-3-5 stopping near ~8k)
# can never truncate the verdict. This was the Mistral blind-test crash: the reviewer
# had no max_tokens, inherited a large budget, and its verbatim-quoting feedback blew
# the cap mid-JSON. Referential critique (no verbatim source) keeps the output small;
# the refiner re-reads the source instead (ADR-021 amended). See ADR-047 call-shape
# taxonomy: bounded-output-by-contract vs large-output→segmented.
REVIEW_VERDICT_MAX_TOKENS: int = 2048

# Interview/enrichment question language-review retries (ADR-038).
# 0 disables the language reviewer (directive-only).
INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES: int = int(
    os.environ.get("INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES", "1")
)

# Token ceiling for a single interview question ("chrome" generation). A question
# is one short sentence, so this is a ceiling, not a target — max_tokens is billed
# on actual usage, so headroom is free. Raised 512 → 4096 (#179): disable_thinking
# is best-effort (Requesty ignored it pre-#179; some models mandate reasoning), and
# on a reasoning model the hidden chain-of-thought shares this budget — at 512 the
# question truncated (finish=length → LLMTruncatedError → the whole turn failed).
# 4096 matches the providers' reasoning-fallback floor; the truncation retry can
# still double it (stays far below TRUNCATION_RETRY_CEILING).
INTERVIEW_QUESTION_MAX_TOKENS: int = 4096

# Token ceiling for CV/LinkedIn → master-profile extraction (and its review/refine
# retries). The output is a full structured profile, and on thinking models the
# reasoning tokens share this budget — at the old 8192 a rich CV truncated mid-JSON
# (finish=length → LLMTruncatedError → /api/profile/upload 500). Extraction keeps
# thinking ON (accuracy matters), so it needs real headroom rather than a small
# floor (ADR-009 amendment, F-B follow-up).
CV_EXTRACTION_MAX_TOKENS: int = 16384

# Token ceiling for generated documents — CV tailoring, cover letter, and the
# language-refinement retries. Same reasoning as extraction: a full tailored CV plus
# a thinking model's reasoning trace share this budget, so the old 8192 truncated
# mid-JSON (finish=length → LLMTruncatedError). max_tokens is a *ceiling* (billed on
# actual usage, not the cap), so the headroom is free unless a generation truly needs
# it; reasoning effort is bounded separately via OPENROUTER_REASONING_EFFORT
# (ADR-009 amendment, F-B follow-up).
CV_GENERATION_MAX_TOKENS: int = 16384

# Token ceiling for the ADR-046 single-call profile reconciler. The whole master
# profile PLUS a whole incoming profile (the import path reconciles a complete CV,
# not a snippet) goes in, and an arbitrarily long batch of typed ops comes out — on a
# rich two-CV + JD merge the op batch is the largest output of any chain. At the old
# 16384 a real two-CV reconcile hit finish=length → LLMTruncatedError, which the engine
# silently swallowed as an empty merge (one CV's content dropped). Raised to 32768 to
# give that merge real headroom; max_tokens is a *ceiling* (billed on actual output),
# so the headroom is free unless a merge truly needs it. NOTE: this MUST stay strictly
# below TRUNCATION_RETRY_CEILING (providers/llm/base.py) or the truncation safety net
# has no room to step up (budget == ceiling → immediate re-raise).
RECONCILE_MAX_TOKENS: int = 32768

# Per-call output budget for *segmented* large generations (ADR-047 / E036). When a
# big generation (CV tailoring, profile reconciliation) is produced in pieces, each
# segment call targets this conservative ceiling so it fits comfortably under the hard
# output cap of capped models (e.g. mistral-medium-3-5 stops near ~8k regardless of a
# 16384/32768 request). The point of segmentation is that *no single call* needs a
# large output, so this stays well under ~8k — raising it would defeat the purpose.
# See ADR-047 §1 (segmentation is the metadata-free stability floor).
SEGMENT_MAX_TOKENS: int = 4096

# Token ceiling for JD analysis (services/job.py). The output is a full structured
# job analysis — role title, required + nice-to-have skills, keywords, culture signals,
# language requirement — which on a dense posting is a sizeable JSON object, and on a
# thinking model the reasoning trace shares the budget. This call previously omitted
# max_tokens and inherited the 4096 provider default, so a rich JD truncated mid-JSON
# (finish=length → LLMTruncatedError → /api/job 500) ~1 in 5 real-LLM calls. Raised to
# the CV ceiling: max_tokens is a *ceiling* billed on actual usage, so the headroom is
# free (ADR-009 amendment, token-budget robustness).
JD_ANALYSIS_MAX_TOKENS: int = 16384

# Token ceiling for gap clustering (services/gap.py cluster_gaps). The output is the
# full list of gap clusters (label + category + member gaps + jd_skills + jd_context),
# which grows with the number of gaps on a rich 2-CV profile vs a thin fixture. Same
# 4096-default truncation as JD analysis; raised to the shared ceiling.
GAP_CLUSTERING_MAX_TOKENS: int = 16384

# Token ceiling for gap analysis pass 2 (services/gap.py _run_analysis). The output is
# one classification (status + reason) per JD requirement, so it scales with the JD's
# requirement count; plus reasoning on a thinking model. Same 4096-default truncation;
# raised to the shared ceiling.
GAP_ANALYSIS_MAX_TOKENS: int = 16384

# Token ceiling for skill-year estimation (services/skill_enrichment.py). The output is
# one estimate per *unmatched* skill, so on a rich profile with many skills it can run
# long; it previously inherited the 4096 default and a truncation silently dropped all
# years (the call is wrapped in try/except → empty estimates). Raised to the shared
# ceiling so the data survives.
SKILL_ESTIMATION_MAX_TOKENS: int = 16384

# Generated-document (CV + cover letter) output-language-review retries (ADR-038).
# Enforces that skill tags + prose land in the target-job language; the tailoring
# directive alone leaks discipline-skill phrases. 0 disables (directive-only).
# 2 (not 1) since #122 follow-up: the language pass is the pipeline's LAST writer
# and carries the US213 coverage gate — with a single retry its refine output would
# ship unreviewed, so a translation that drops a covered surface form (e.g.
# "efficiency improvement" → "Effizienzsteigerung") could never be caught.
CV_LANGUAGE_REVIEW_MAX_RETRIES: int = int(
    os.environ.get("CV_LANGUAGE_REVIEW_MAX_RETRIES", "2")
)

# Master Profile Health — post-merge severity classifier thresholds
# (US162 / E033 / ADR-041 amended; tunable per the ADR-035 precedent).
# Total reconciliation delta (extracted-but-not-stored data points, summed across
# work/skills/certs/education) STRICTLY ABOVE this → critical; a smaller non-zero
# loss stays a dismissible review. Set MERGE_DATALOSS_CRITICAL_THRESHOLD to override.
MERGE_DATALOSS_CRITICAL_THRESHOLD: int = int(
    os.environ.get("MERGE_DATALOSS_CRITICAL_THRESHOLD", "3")
)
# Merge confidence BELOW this → review. Set MERGE_CONFIDENCE_REVIEW_THRESHOLD to override.
MERGE_CONFIDENCE_REVIEW_THRESHOLD: float = float(
    os.environ.get("MERGE_CONFIDENCE_REVIEW_THRESHOLD", "0.75")
)

# CV skills section — JD-aware selection cap (#192). The tailored CV must present a
# prioritised, JD-relevant SUBSET of the master profile's skills, not dump the whole
# thing (~70 tags). services.cv._tailor_skills_to_jd ranks the candidate's skills by
# relevance to the JobAnalysis (required > nice-to-have > keyword) and keeps at most
# this many — JD-required skills that exist in the profile are always kept even past
# the cap, and no-relevance tags are dropped first when over it. Env-overridable.
CV_MAX_SKILLS: int = int(os.environ.get("CV_MAX_SKILLS", "24"))

# Master Profile pre-merge snapshots (US168 / E033 / ADR-042). Bounded per profile;
# the most-recent N survive, older ones are pruned. Env-overridable.
SNAPSHOT_MAX_PER_PROFILE: int = int(
    os.environ.get("SNAPSHOT_MAX_PER_PROFILE", "10")
)

# GDPR retention TTLs — configurable via environment variables (ADR-005 amendment, Sprint 25)
GENERATED_DOCUMENTS_TTL_DAYS: int = int(os.environ.get("GENERATED_DOCUMENTS_TTL_DAYS", "90"))
# Cancelled-application grace window (US222/issue #158, ADR-005 amendment
# 2026-07-13). Cancelling an application re-arms its inactivity timer to this
# many days; after the tombstone, its generated documents (incl. submitted
# pins) are purged regardless of GENERATED_DOCUMENTS_TTL_DAYS. 0 = disabled
# (cancelled applications keep the normal inactivity TTL). Admin-UI
# configurability is parked for the Admin persona (Strawberry).
CANCELLED_APPLICATION_TTL_DAYS: int = int(os.environ.get("CANCELLED_APPLICATION_TTL_DAYS", "7"))
INTERVIEW_SESSION_TTL_DAYS: int = int(os.environ.get("INTERVIEW_SESSION_TTL_DAYS", "30"))
UPLOAD_TTL_DAYS: int = int(os.environ.get("UPLOAD_TTL_DAYS", "7"))
PROFILE_INACTIVITY_TTL_DAYS: int = int(os.environ.get("PROFILE_INACTIVITY_TTL_DAYS", "730"))

# Orphan-file grace period (issue #152, dFMEA SF-PROFILE.5). The retention
# worker's orphan scan skips files younger than this: the upload flow saves
# the file BEFORE committing its DB row, so a young unreferenced file may be
# an in-flight upload, not an orphan.
ORPHAN_FILE_GRACE_HOURS: int = int(os.environ.get("ORPHAN_FILE_GRACE_HOURS", "1"))

# ── Truthfulness Oracle (ADR-052 / E043) ─────────────────────────────────────
# Deterministic-first: these caps bound the ONLY two LLM touchpoints the
# Oracle has (free-prose segmentation fallback + narrow stance entailment).
# A raw prose block only goes to the ADR-047 segmentation fallback when it is
# longer than this AND deterministic sentence splitting found no boundaries.
ORACLE_PROSE_FALLBACK_CHARS: int = int(os.environ.get("ORACLE_PROSE_FALLBACK_CHARS", "600"))
# Output cap for a single Oracle segmentation call (ADR-047 bounded-by-contract).
ORACLE_SEGMENT_MAX_TOKENS: int = int(os.environ.get("ORACLE_SEGMENT_MAX_TOKENS", "800"))
# Output cap for a single narrow entailment verdict call.
ORACLE_ENTAILMENT_MAX_TOKENS: int = int(os.environ.get("ORACLE_ENTAILMENT_MAX_TOKENS", "200"))
# Hard cap on entailment calls per audited document (ADR-052: "narrow and
# capped") — claims beyond the cap fall back to the deterministic verdict.
ORACLE_MAX_ENTAILMENT_CALLS: int = int(os.environ.get("ORACLE_MAX_ENTAILMENT_CALLS", "10"))
