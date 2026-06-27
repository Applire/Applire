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

# Interview/enrichment question language-review retries (ADR-038).
# 0 disables the language reviewer (directive-only).
INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES: int = int(
    os.environ.get("INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES", "1")
)

# Token ceiling for a single interview question ("chrome" generation). A question
# is one short sentence, so this is a ceiling, not a target. Raised from 256 to give
# thinking models headroom for the answer; paired with disable_thinking on these
# calls so reasoning tokens don't eat the budget (ADR-009 amendment, F-B).
INTERVIEW_QUESTION_MAX_TOKENS: int = 512

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
CV_LANGUAGE_REVIEW_MAX_RETRIES: int = int(
    os.environ.get("CV_LANGUAGE_REVIEW_MAX_RETRIES", "1")
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

# Master Profile pre-merge snapshots (US168 / E033 / ADR-042). Bounded per profile;
# the most-recent N survive, older ones are pruned. Env-overridable.
SNAPSHOT_MAX_PER_PROFILE: int = int(
    os.environ.get("SNAPSHOT_MAX_PER_PROFILE", "10")
)

# GDPR retention TTLs — configurable via environment variables (ADR-005 amendment, Sprint 25)
GENERATED_DOCUMENTS_TTL_DAYS: int = int(os.environ.get("GENERATED_DOCUMENTS_TTL_DAYS", "90"))
INTERVIEW_SESSION_TTL_DAYS: int = int(os.environ.get("INTERVIEW_SESSION_TTL_DAYS", "30"))
UPLOAD_TTL_DAYS: int = int(os.environ.get("UPLOAD_TTL_DAYS", "7"))
PROFILE_INACTIVITY_TTL_DAYS: int = int(os.environ.get("PROFILE_INACTIVITY_TTL_DAYS", "730"))
