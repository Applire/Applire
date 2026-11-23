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
    # ADR-096 phase 2 (WP-A): what is left, the plan, a (simulated) purchase.
    "GET /api/me/allowance",
    "PUT /api/me/plan",
    "POST /api/me/purchases",
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


# ── ADR-096 phase 2: the debited doors (cl. 7 as ruled, MD-G6) ────────────────
#
# A door that starts an application (a CV or a letter of a job) or a profile
# import must reach ``allowance.debit_for_door``; no other door may. Shown
# statically: from the route's endpoint / the tool's body, every call that
# resolves to an ``applire.*`` function is followed (direct calls only — a
# function handed to ``add_task`` is not followed, so background work never
# counts as the door's debit). The dynamic half, per door, is
# ``backend/tests/unit/test_adr096_debit_doors.py``.

#: route → the metered action it debits.
METERED = {
    "POST /api/cv/generate": "application",
    "POST /api/cover-letter/generate": "application",
    "POST /api/profile/import-jobs": "profile_import",
}
METERED_TOOLS = {
    "generate_cv": "application",
    "generate_cover_letter": "application",
    "import_cv": "profile_import",
}

_DEBIT = "debit_for_door"


def _function_ast(fn):
    import ast
    import inspect
    import textwrap

    try:
        source = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return None
    tree = ast.parse(source)
    return tree.body[0] if tree.body else None


def _resolve(node, fn, local_imports):
    """The object an ``ast`` call target names in ``fn``'s module, or None."""
    import ast
    import importlib

    if isinstance(node, ast.Name):
        if node.id in local_imports:
            module, attr = local_imports[node.id]
            try:
                mod = importlib.import_module(module)
            except ImportError:
                return None
            return getattr(mod, attr, None) if attr else mod
        return fn.__globals__.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _resolve(node.value, fn, local_imports)
        return getattr(base, node.attr, None) if base is not None else None
    return None


def _debited_actions(fn, _memo=None, _depth=0):
    """The metered actions ``fn`` debits, directly or through the ``applire``
    functions it calls (a string literal action argument of the call)."""
    import ast
    import inspect

    memo = {} if _memo is None else _memo
    fn = inspect.unwrap(fn)
    key = getattr(fn, "__code__", None) or id(fn)
    if key in memo:
        return memo[key]
    memo[key] = set()
    if _depth > 8 or not (getattr(fn, "__module__", "") or "").startswith("applire."):
        return memo[key]
    tree = _function_ast(fn)
    if tree is None:
        return memo[key]
    local_imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local_imports[alias.asname or alias.name] = (node.module, alias.name)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if name == _DEBIT:
            action = node.args[2] if len(node.args) > 2 else None
            found.add(action.value if isinstance(action, ast.Constant) else "?")
            continue
        target = _resolve(node.func, fn, local_imports)
        if inspect.isfunction(target) or inspect.ismethod(target):
            found |= _debited_actions(target, memo, _depth + 1)
    memo[key] = found
    return found


def test_every_metered_door_reaches_the_debit_and_no_other_door_does():
    from applire.main import app

    assert set(METERED) <= MODEL_WORK and set(METERED_TOOLS) <= MODEL_WORK_TOOLS
    seen = {}
    memo: dict = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods):
                seen[f"{method} {route.path}"] = _debited_actions(route.endpoint, memo)
    missing = {k: a for k, a in METERED.items() if seen.get(k) != {a}}
    extra = {k: a for k, a in seen.items() if a and k not in METERED}
    assert missing == {}, "a metered door no longer reaches debit_for_door (ADR-096 cl. 7)"
    assert extra == {}, "a new door debits: name it in METERED (and give it a seam test)"


#: A metered tool with more than one way in (``import_cv``: a PDF and a text):
#: every call that starts the work must come after a statement that debits, on
#: its own path — one debited branch must not cover the other.
METERED_TOOL_ENTRIES = {"import_cv": {"import_from_pdf", "import_from_text"}}


def _undebited_entries(fn, entries):
    """The entry calls in ``fn``'s body with no debiting statement before them
    on their path (simple statements only count as debiting)."""
    import ast
    import inspect

    fn = inspect.unwrap(fn)
    tree = _function_ast(fn)
    local_imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local_imports[alias.asname or alias.name] = (node.module, alias.name)
    memo: dict = {}

    def calls(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                yield sub

    def name_of(call):
        return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", None)

    def debits(stmt):
        for call in calls(stmt):
            if name_of(call) == _DEBIT:
                return True
            target = _resolve(call.func, fn, local_imports)
            if (inspect.isfunction(target) or inspect.ismethod(target)) and _debited_actions(target, memo):
                return True
        return False

    def blocks(stmt):
        for _, value in ast.iter_fields(stmt):
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                yield value
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.ExceptHandler):
                        yield item.body
                    elif hasattr(item, "body") and isinstance(getattr(item, "body"), list):
                        yield item.body  # match_case

    bad = []

    def visit(stmts, covered):
        for stmt in stmts:
            children = list(blocks(stmt))
            if children:
                for block in children:
                    visit(block, covered)
                continue
            if not covered:
                bad.extend(name_of(c) for c in calls(stmt) if name_of(c) in entries)
            if debits(stmt):
                covered = True

    visit(tree.body, False)
    return bad


def test_every_way_into_a_metered_tool_is_debited():
    from applire.mcp.server import mcp

    tools = {t.name: t.fn for t in mcp._tool_manager.list_tools()}
    for name, entries in METERED_TOOL_ENTRIES.items():
        undebited = _undebited_entries(tools[name], entries)
        assert undebited == [], f"{name}: {undebited} start work no debit precedes (ADR-096 cl. 7)"
        # the entries are still how the tool starts its work (a rename fails here)
        import ast

        called = {
            c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", None)
            for c in ast.walk(_function_ast(__import__("inspect").unwrap(tools[name])))
            if isinstance(c, ast.Call)
        }
        assert entries <= called, (name, entries - called)


def test_every_metered_tool_reaches_the_debit_and_no_other_tool_does():
    from applire.mcp.server import mcp

    memo: dict = {}
    seen = {t.name: _debited_actions(t.fn, memo) for t in mcp._tool_manager.list_tools()}
    missing = {k: a for k, a in METERED_TOOLS.items() if seen.get(k) != {a}}
    extra = {k: a for k, a in seen.items() if a and k not in METERED_TOOLS}
    assert missing == {}, "a metered tool no longer reaches debit_for_door (ADR-096 cl. 7)"
    assert extra == {}, "a new tool debits: name it in METERED_TOOLS (and give it a seam test)"
