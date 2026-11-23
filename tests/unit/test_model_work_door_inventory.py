# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every REST route and every MCP tool is named in exactly one door list
(ADR-096 cl. 4; ruling C-1, ADR-091 amended 2026-11-18).

* MODEL_WORK: the route starts model work (the LLM provider, OCR through
  the provider, embeddings of an import). It depends on admit_model_work
  and is refused before its body runs (409 log_consent_required /
  provider_key_required / an allowance code).
* LAYERED: a write that works without model work and layers a model step
  on top (the skill estimate of a profile edit, the truthfulness and critic
  re-audit after a document edit, the preview of an entry merge, the merge of
  a held import). It depends on try_admit_model_work: the write runs for
  everyone, the model step only for an admitted person, and a branch that is
  model work raises the refusal.
* NO_MODEL_WORK: reads, account and admin routes, deterministic writes.

A new route that is in none of the lists fails here, so its author has to say
which door it needs. The provider factory refuses an owner whose payer facts
do not admit model work in any case (the safety net), so a misfiled route is
refused there; the lists make the door explicit and testable.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "mistral")

from fastapi.routing import APIRoute  # noqa: E402

#: Refused at the door when model work is not admitted.
MODEL_WORK = frozenset({
    "POST /api/cover-letter/generate",
    "POST /api/cover-letter/{doc_id}/review/add-evidence",
    "POST /api/cover-letter/{doc_id}/review/as-goal",
    "POST /api/cover-letter/{doc_id}/review/move",
    "POST /api/cover-letter/{doc_id}/review/recheck",
    "POST /api/cover-letter/{doc_id}/review/removal-evidence",
    "POST /api/cover-letter/{doc_id}/review/shorten",
    "POST /api/cover-letter/{doc_id}/review/take-out",
    "POST /api/cover-letter/{doc_id}/review/translate",
    "POST /api/cv/generate",
    "PATCH /api/cv/{cv_id}/sections/{section_id}/assist",
    "POST /api/cv/{cv_id}/sections/{section_id}/assist",
    "POST /api/cv/{cv_id}/sections/{section_id}/assist/accept",
    "POST /api/cv/{cv_id}/sections/{section_id}/rewrite",
    "POST /api/cv/{doc_id}/review/add-evidence",
    "POST /api/cv/{doc_id}/review/as-goal",
    "POST /api/cv/{doc_id}/review/move",
    "POST /api/cv/{doc_id}/review/recheck",
    "POST /api/cv/{doc_id}/review/removal-evidence",
    "POST /api/cv/{doc_id}/review/shorten",
    "POST /api/cv/{doc_id}/review/take-out",
    "POST /api/cv/{doc_id}/review/translate",
    "POST /api/job/analyze",
    "POST /api/job/{job_id}/gap-jobs",
    "POST /api/job/{job_id}/gaps/refresh",
    "POST /api/profile/conflicts/{conflict_id}/merge-proposal",
    "POST /api/profile/enrich/start",
    "POST /api/profile/enrich/{session_id}/na",
    "POST /api/profile/enrich/{session_id}/respond",
    "POST /api/profile/enrich/{session_id}/skip",
    "POST /api/profile/import-jobs",
    "POST /api/profile/testimony",
    "POST /api/session",
    "POST /api/session/profile-review",
    "POST /api/session/{session_id}/analyze-gaps",
    "POST /api/session/{session_id}/merge-proposal",
    "POST /api/session/{session_id}/message",
})


#: The write runs for everyone; its model step only when admitted.
LAYERED = frozenset({
    "PATCH /api/cover-letter/{cl_id}/section",
    "POST /api/cover-letter/{doc_id}/review/edited",
    "POST /api/cover-letter/{doc_id}/review/keep-length",
    "POST /api/cover-letter/{doc_id}/review/supported",
    "POST /api/cover-letter/{doc_id}/review/undo",
    "POST /api/cv/{cv_id}/page-overflow",
    "PATCH /api/cv/{cv_id}/sections/{section_id:path}",
    "POST /api/cv/{doc_id}/review/edited",
    "POST /api/cv/{doc_id}/review/keep-length",
    "POST /api/cv/{doc_id}/review/supported",
    "POST /api/cv/{doc_id}/review/undo",
    "POST /api/profile/entries/merge",
    "POST /api/profile/staged/{staged_id}/resolve",
    "PATCH /api/profile/{section}",
})


#: No model work.
NO_MODEL_WORK = frozenset({
    "GET /api/admin/audit",
    "GET /api/admin/color-schemes",
    "POST /api/admin/color-schemes",
    "GET /api/admin/color-schemes/active",
    "POST /api/admin/color-schemes/preview",
    "DELETE /api/admin/color-schemes/{scheme_id}",
    "PATCH /api/admin/color-schemes/{scheme_id}/activate",
    "PATCH /api/admin/color-schemes/{scheme_id}/offer",
    "GET /api/admin/dashboard",
    "GET /api/admin/notices",
    "GET /api/admin/probe-tokens",
    "POST /api/admin/probe-tokens",
    "DELETE /api/admin/probe-tokens/{token_id}",
    "GET /api/admin/settings",
    "PUT /api/admin/settings",
    "DELETE /api/admin/settings/{key}",
    "GET /api/admin/usage",
    "GET /api/admin/users",
    "POST /api/admin/users",
    "DELETE /api/admin/users/{user_id}",
    "PATCH /api/admin/users/{user_id}",
    "POST /api/admin/users/{user_id}/reinvite",
    "POST /api/admin/users/{user_id}/reset-link",
    "POST /api/admin/users/{user_id}/revoke-tokens",
    "GET /api/applications",
    "POST /api/applications",
    "DELETE /api/applications/{application_id}",
    "GET /api/applications/{application_id}",
    "PATCH /api/applications/{application_id}",
    "POST /api/applications/{application_id}/mark-hired",
    "POST /api/applications/{application_id}/pins",
    "DELETE /api/applications/{application_id}/pins/{pin_id}",
    "POST /api/applications/{application_id}/start",
    "POST /api/auth/forgot",
    "POST /api/auth/links/inspect",
    "POST /api/auth/links/redeem",
    "POST /api/auth/login",
    "POST /api/auth/logout",
    "GET /api/auth/me",
    "GET /api/auth/oidc/callback",
    "GET /api/auth/oidc/start",
    "POST /api/auth/password",
    "GET /api/auth/state",
    "GET /api/color-schemes",
    "GET /api/color-schemes/effective",
    "GET /api/cover-letter/by-job/{job_id}",
    "GET /api/cover-letter/{cl_id}/ats-report",
    "GET /api/cover-letter/{cl_id}/critic-report",
    "GET /api/cover-letter/{cl_id}/docx",
    "GET /api/cover-letter/{cl_id}/html",
    "GET /api/cover-letter/{cl_id}/pdf",
    "PATCH /api/cover-letter/{cl_id}/signature",
    "GET /api/cover-letter/{cl_id}/status",
    "GET /api/cover-letter/{cl_id}/truthfulness-report",
    "POST /api/cover-letter/{doc_id}/review/kept",
    "POST /api/cover-letter/{doc_id}/review/stations",
    "POST /api/cover-letter/{doc_id}/review/walked",
    "GET /api/cv",
    "GET /api/cv/{cv_id}/ats-report",
    "PATCH /api/cv/{cv_id}/color",
    "GET /api/cv/{cv_id}/critic-report",
    "GET /api/cv/{cv_id}/docx",
    "GET /api/cv/{cv_id}/html",
    "POST /api/cv/{cv_id}/page-overflow/preview",
    "GET /api/cv/{cv_id}/pdf",
    "GET /api/cv/{cv_id}/profile-diff",
    "GET /api/cv/{cv_id}/sections",
    "PATCH /api/cv/{cv_id}/signature",
    "GET /api/cv/{cv_id}/status",
    "GET /api/cv/{cv_id}/truthfulness-report",
    "POST /api/cv/{doc_id}/review/kept",
    "POST /api/cv/{doc_id}/review/stations",
    "POST /api/cv/{doc_id}/review/walked",
    "GET /api/documents",
    "POST /api/flow",
    "POST /api/flow/{flow_id}/advance",
    "GET /api/flow/{flow_id}/state",
    "GET /api/job/{job_id}",
    "GET /api/job/{job_id}/gap-jobs/{gap_job_id}",
    "GET /api/job/{job_id}/gaps",
    "POST /api/job/{job_id}/gaps/liabilities/downgrade",
    "POST /api/job/{job_id}/gaps/{cluster_id}/left-open",
    "GET /api/job/{job_id}/posting-terms",
    "POST /api/job/{job_id}/posting-terms",
    "GET /api/jobs/match",
    "DELETE /api/me/account",
    "DELETE /api/me/log-consent",
    "GET /api/me/log-consent",
    "POST /api/me/log-consent",
    "DELETE /api/me/oidc",
    "POST /api/me/oidc/link",
    "DELETE /api/me/provider-key",
    "GET /api/me/provider-key",
    "PUT /api/me/provider-key",
    "POST /api/me/reauth/start",
    "GET /api/me/tokens",
    "POST /api/me/tokens",
    "DELETE /api/me/tokens/{token_id}",
    "GET /api/ops/health",
    "DELETE /api/profile",
    "GET /api/profile",
    "POST /api/profile/aspirations/answer",
    "PUT /api/profile/availability",
    "POST /api/profile/availability/reminder/dismiss",
    "GET /api/profile/changes",
    "GET /api/profile/changes/summary",
    "POST /api/profile/confirmations/resolve",
    "POST /api/profile/conflicts/{conflict_id}/resolve",
    "POST /api/profile/denials/withdraw",
    "GET /api/profile/enrichment-history",
    "POST /api/profile/entries/keep-separate",
    "GET /api/profile/exists",
    "GET /api/profile/export",
    "GET /api/profile/health",
    "POST /api/profile/health/recompute-span",
    "POST /api/profile/health/unit",
    "POST /api/profile/import",
    "GET /api/profile/import-capabilities",
    "GET /api/profile/import-jobs",
    "GET /api/profile/import-jobs/{import_id}",
    "GET /api/profile/import-summary",
    "POST /api/profile/matches/confirm",
    "POST /api/profile/matches/separate",
    "POST /api/profile/not-applied/adopt",
    "DELETE /api/profile/photo",
    "GET /api/profile/photo",
    "POST /api/profile/photo",
    "PUT /api/profile/photo/crop",
    "POST /api/profile/replaced-bullets/dismiss",
    "POST /api/profile/replaced-bullets/undo",
    "POST /api/profile/restated/apply",
    "POST /api/profile/roles",
    "DELETE /api/profile/signature",
    "GET /api/profile/signature",
    "POST /api/profile/signature",
    "POST /api/profile/undo-last-merge",
    "POST /api/profile/upload",
    "GET /api/profile/uploads",
    "GET /api/session/{session_id}",
    "GET /api/settings",
    "PATCH /api/settings",
    "POST /api/settings/upgrade-notice/dismiss",
    "POST /api/setup",
    "GET /docs",
    "GET /health",
    "GET /openapi.json",
    "GET /redoc",
})


#: MCP tools: admits="model_work".
MODEL_WORK_TOOLS = frozenset({
    "analyze_gaps", "analyze_jd", "audit_document", "generate_cover_letter", "generate_cv",
    "import_cv", "resolve_gap", "run_interview", "send_message", "submit_claims",
    "submit_testimony",
})
#: MCP tools that layer a model step (see LAYERED); no admits, the tool
#: body asks allowance itself or the factory decides.
LAYERED_TOOLS = frozenset({"update_profile", "resolve_held_merge", "render_document"})
NO_MODEL_WORK_TOOLS = frozenset({
    "add_role", "advance_flow", "confirm_posting_terms", "create_application", "decide_open_points",
    "get_application",
    "get_cover_letter_ats_report", "get_cover_letter_status", "get_cv_ats_report", "get_cv_status",
    "get_flow_state", "get_guide", "get_profile", "get_profile_health", "list_applications",
    "start_flow", "undo_last_merge", "update_application",
})


def _deps(dependant):
    yield dependant.call
    for sub in dependant.dependencies:
        yield from _deps(sub)


def _routes():
    from applire.main import app

    for route in app.routes:
        if isinstance(route, APIRoute):
            calls = set(_deps(route.dependant))
            for method in sorted(route.methods):
                yield f"{method} {route.path}", calls


def test_the_lists_do_not_overlap():
    assert not (MODEL_WORK & LAYERED) and not (MODEL_WORK & NO_MODEL_WORK) and not (LAYERED & NO_MODEL_WORK)
    assert not (MODEL_WORK_TOOLS & LAYERED_TOOLS) and not (MODEL_WORK_TOOLS & NO_MODEL_WORK_TOOLS)
    assert not (LAYERED_TOOLS & NO_MODEL_WORK_TOOLS)


def test_every_route_is_named_once_and_has_its_door():
    from applire.auth.deps import admit_model_work, try_admit_model_work

    seen, unlisted, wrong = set(), [], []
    for key, calls in _routes():
        seen.add(key)
        strict, optional = admit_model_work in calls, try_admit_model_work in calls
        if key in MODEL_WORK:
            ok = strict
        elif key in LAYERED:
            ok = optional and not strict
        elif key in NO_MODEL_WORK:
            ok = not strict and not optional
        else:
            unlisted.append(key)
            continue
        if not ok:
            wrong.append(key)
    assert unlisted == [], "a new route: put it in MODEL_WORK, LAYERED or NO_MODEL_WORK"
    assert wrong == []
    assert (MODEL_WORK | LAYERED | NO_MODEL_WORK) - seen == set(), "a listed route no longer exists"


def _admits(fn):
    action = None
    while fn is not None:
        action = getattr(fn, "__applire_admits__", action)
        fn = getattr(fn, "__wrapped__", None)
    return action


def test_every_mcp_tool_is_named_once_and_has_its_door():
    from applire.mcp.server import mcp

    tools = {t.name: _admits(t.fn) for t in mcp._tool_manager.list_tools()}
    assert set(tools) == MODEL_WORK_TOOLS | LAYERED_TOOLS | NO_MODEL_WORK_TOOLS
    assert {n for n, a in tools.items() if a == "model_work"} == MODEL_WORK_TOOLS
