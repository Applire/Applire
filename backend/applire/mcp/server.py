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

"""
Applire MCP Server — Iteration 8 — Agent Flow & Ingestion (ADR-010 amended 2026-05-31)

Exposes the full JD → profile → gap-fill → CV tailoring workflow as MCP tools
and resources so AI agents can drive the process autonomously.

Transport: stdio (Community Edition).  SSE is reserved for Cloud Edition.

Tools:
  analyze_jd        — analyse a job description text or URL
  get_profile       — retrieve the current MasterProfile
  update_profile    — patch a section of the MasterProfile
  analyze_gaps      — compare profile against a job
  run_interview     — start a gap-fill interview session
  send_message      — advance an active interview session
  generate_cv       — generate a tailored CV
  get_cv_status     — poll CV generation status and retrieve download URLs
  get_cv_ats_report — get the persisted ATS audit report for a generated CV
  generate_cover_letter       — generate a cover letter for a job (#170)
  get_cover_letter_status     — poll cover letter generation status (#170)
  get_cover_letter_ats_report — ATS audit report for a generated cover letter (#170)
  (get_cover_letter_status also carries critic_report — ADR-060 Pass B, #322)
  start_flow        — create or resume a flow session (US109)
  advance_flow      — advance a flow to the next step (US109)
  get_flow_state    — get current flow session state (US109)
  import_cv         — seed or extend the Master Profile from a PDF or CV text
  add_role          — add a new work-experience role to the Master Profile
  get_guide         — the agent-usage guide + honesty contract (ADR-056)
  create_application — create a new job application record
  update_application — update user-managed fields (status, notes, deadline, source_url, submitted documents, stale-CV dismiss, fact pins)
  list_applications  — list all job applications for the current user
  get_application    — retrieve a single job application by ID
  submit_testimony   — reconcile a whole free-text testimony document into the profile (#258)

Resources:
  profile://current       — current MasterProfile JSON
  job://{job_id}          — JobAnalysis JSON
  cv://{cv_id}            — GeneratedCV metadata JSON
  flow://{flow_id}        — FlowStateResponse JSON
  schema://cv             — public tailored-CV content contract (ADR-054)
  schema://cover-letter   — public cover-letter content contract (ADR-054)
  schema://claims         — public agent-testimony contract (ADR-054, E045)
  schema://testimony      — public free-text testimony contract (#258)
  guide://usage           — the agent-usage guide + honesty contract (ADR-056)

Prompts:
  how-to-use-applire      — the guide as a prompt (human slash-command discovery)
"""

import base64
import binascii
import functools
import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta
from importlib import resources as importlib_resources

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from applire.config import settings
from applire.constants import MAX_TARGET_PAGES
from applire.exceptions import LLMTruncatedError
from applire.mcp.deps import get_db
from applire.mcp.errors import internal, invalid_input, not_found
from applire.services.profile.commit import StaleEditError
from applire.models.application import UserStatus
from applire.models.cover_letter import GeneratedCoverLetter
from applire.models.cv import GeneratedCV
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.models.user import User
from applire.norms import DEFAULT_REGION, REGION_NORMS
from applire.providers import get_provider
from applire.schemas.application import (
    AddFactPinRequest,
    ApplicationListResponse,
    ApplicationResponse,
    CreateApplicationRequest,
    PatchApplicationRequest,
)
from applire.schemas.cover_letter import CoverLetterGenerateRequest
from applire.schemas.cv import GeneratedCVResponse
from applire.schemas.job import JobAnalysisResponse
from applire.schemas.flow import AdvanceFlowRequest, CreateFlowRequest
from applire.schemas.profile_roles import AddRoleRequest, CloseRoleEntry
from applire.services.profile.role_add import add_role_to_profile, AddRoleValidationError
from applire.services.scraper import ScraperError, scrape_job_url
from applire.services import application as app_svc
from applire.services import fact_pins as pin_svc
from applire.services import cover_letter as cover_letter_svc
from applire.services import cv as cv_svc
from applire.services import gap as gap_svc
from applire.services import job as job_svc
from applire.services import oracle as oracle_svc
from applire.services import profile as profile_svc
from applire.services import session as session_svc
from applire.services.flow import orchestrator as flow_svc
from applire.services.flow.orchestrator import ArtifactRequiredError, InvalidTransitionError

MAX_CV_BYTES = 10 * 1024 * 1024  # 10 MB pre-encode cap (ADR-010 amendment)

# Date-stamped revision of AGENT_GUIDE.md so callers can cache (ADR-056).
GUIDE_VERSION = "2026-08-25"

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _load_guide() -> str:
    """The canonical agent-usage guide + honesty contract (ADR-056).

    Package data, not a repo-relative path — must survive a wheel install.
    """
    return (
        importlib_resources.files("applire.mcp")
        .joinpath("AGENT_GUIDE.md")
        .read_text(encoding="utf-8")
    )


# ~120 tokens by design: this string rides the initialize handshake into
# clients that inject server instructions, so even a guide-skipping agent
# sees the honesty core (ADR-056 §1d).
_INSTRUCTIONS = (
    "Applire is the candidate's job-application tool: it keeps their master "
    "profile (the vault), verifies documents against it, and renders "
    "norms-checked CVs and cover letters. Call get_guide before your first "
    "application run — it carries the tool flow and Applire's honesty "
    "contract. Core rule: ground every claim in the candidate's own data; "
    "never fabricate. Surface genuine gaps to the human instead of papering "
    "over them."
)

mcp = FastMCP("Applire", instructions=_INSTRUCTIONS)


@mcp.tool(
    description=(
        "Return the Applire agent-usage guide + honesty contract (markdown). "
        "Call this before your first application run; re-fetch on reconnect. "
        "Returns {guide, version}."
    )
)
async def get_guide() -> dict:
    return {"guide": _load_guide(), "version": GUIDE_VERSION}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def warn_if_base_url_unset() -> None:
    """Warn at server startup when APPLIRE_BASE_URL was never set (#168).

    Every MCP tool that returns an artifact link (generate_cv, get_cv_status,
    generate_cover_letter, ...) builds html_url/pdf_url from
    ``settings.applire_base_url``, which defaults to
    ``http://localhost:8001``. Behind a reverse proxy (nginx, Caddy, ...) that
    default silently points agents at an unreachable URL instead of the
    externally-reachable one.

    pydantic-settings can't distinguish "the deployer set it to the default
    value" from "the deployer never set it" — so this checks os.environ
    directly rather than settings.applire_base_url.
    """
    if "APPLIRE_BASE_URL" not in os.environ:
        logger.warning(
            "APPLIRE_BASE_URL is not set — MCP artifact URLs (html_url/pdf_url) "
            "will default to %s, which will be wrong for any non-default "
            "deployment. Set APPLIRE_BASE_URL to the externally-reachable "
            "scheme://host:port of your reverse proxy (see .env.example).",
            settings.applire_base_url,
        )


def _parse_uuid(value: str, param: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise invalid_input(f"{param} must be a valid UUID, got: {value!r}")


def _profile_summary(profile_response) -> dict:
    """Non-sensitive extraction summary for agents — never the raw profile."""
    data = profile_response.model_dump(mode="json")
    profile = data.get("profile") or {}
    stats = data.get("stats") or {}
    return {
        "profile_id": data.get("id"),
        "positions": stats.get("positions"),
        "skills_count": len(profile.get("skills") or []),
        "completeness": data.get("completeness"),
        "merge_conflicts": len(data.get("merge_conflicts") or []),
    }


async def _current_user_id(db) -> uuid.UUID:
    """Resolve the single local user (Community single-user mode)."""
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if user is None:
        raise not_found("No user found — import a CV first via import_cv")
    return user.id


# ---------------------------------------------------------------------------
# Tools (7.2 – 7.8)
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Seed or extend the Master Profile from a CV. Provide file_base64 "
        "(base64 PDF, <=10 MB) or text (extracted CV text). Call once per CV "
        "to merge several. Returns a summary, never the raw profile."
    )
)
async def import_cv(
    file_base64: str | None = None,
    filename: str | None = None,
    text: str | None = None,
) -> dict:
    # `filename` is reserved (arc42 §5.3.6a) for a future format hint; ignored for now.
    provider = get_provider()
    if file_base64:
        try:
            raw = base64.b64decode(file_base64, validate=True)
        except (binascii.Error, ValueError):
            raise invalid_input("file_base64 is not valid base64")
        if len(raw) > MAX_CV_BYTES:
            raise invalid_input(
                "CV exceeds 10 MB after decoding — "
                "upload large files via REST POST /api/profile/upload instead."
            )
        async with get_db() as db:
            try:
                result = await profile_svc.import_from_pdf(raw, db, provider)
            except ValueError as exc:
                raise invalid_input(str(exc))
            except Exception as exc:
                raise internal(str(exc))
    elif text and text.strip():
        async with get_db() as db:
            try:
                result = await profile_svc.import_from_text(text.strip(), db, provider)
            except ValueError as exc:
                raise invalid_input(str(exc))
            except Exception as exc:
                raise internal(str(exc))
    else:
        raise invalid_input("Provide either file_base64 (base64 PDF) or text")
    return _profile_summary(result)


@mcp.tool(
    description=(
        "Analyse a job description and return a structured JobAnalysis. "
        "Provide exactly one of: text (the JD body) or url (scraped "
        "server-side). Pass role_title/company_name to override the values "
        "inferred from the body (when the board lists them separately). "
        "May return a duplicate_of hint (see guide). jd_language = detected "
        "document language (see guide)."
    )
)
async def analyze_jd(
    text: str | None = None,
    url: str | None = None,
    role_title: str | None = None,
    company_name: str | None = None,
) -> dict:
    if not text and not url:
        raise invalid_input("Provide either text or url")
    if text and url:
        raise invalid_input("Provide only one of text or url")
    provider = get_provider()
    source_url = None
    if url:
        try:
            jd_text = await scrape_job_url(url)
        except ScraperError as exc:
            raise invalid_input(f"Could not scrape {url}: {exc}")
        source_url = url
    else:
        jd_text = text.strip()
        if not jd_text:
            raise invalid_input("text must not be empty")
    async with get_db() as db:
        try:
            result = await job_svc.analyze_jd(
                jd_text,
                db,
                provider,
                source_url=source_url,
                role_title_override=role_title,
                company_name_override=company_name,
            )
        except Exception as exc:
            raise internal(str(exc))
        # Branch F (E039/US220): repost hint against the user's own pipeline.
        # Best-effort — no user yet (fresh install) or any lookup failure just
        # skips the hint; the analysis itself must never fail because of it.
        try:
            uid = await _current_user_id(db)
            result.duplicate_of = await app_svc.find_duplicate_application(
                uid,
                job_analysis_id=result.id,
                source_url=source_url,
                raw_text=jd_text,
                db=db,
            )
        except Exception:
            pass
    return result.model_dump(mode="json")


@mcp.tool(description="Return the current MasterProfile.")
async def get_profile() -> dict:
    async with get_db() as db:
        result = await profile_svc.get_profile(db)
    if result is None:
        raise not_found("No profile found — import a CV first via POST /api/profile/import")
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Update one MasterProfile section. "
        f"section: one of {', '.join(sorted(profile_svc._VALID_SECTIONS))}. "
        "Lists REPLACED WHOLESALE, objects PATCHED. "
        "basis_updated_at: get_profile's updated_at; stale → refused."
    )
)
async def update_profile(
    section: str, data: dict | list, basis_updated_at: str | None = None
) -> dict:
    # #337 / ADR-058 clause 2 — the REST route passes a provider and this tool
    # did not, so `patch_profile_section`'s `if provider is not None` gate meant
    # a skills edit was enriched through the UI and silently not through the
    # agent door. Same intake, different vault state, decided by entry path.
    provider = get_provider()
    # ADR-063 amended 2026-08-25 (ADR-058 parity): the same OPTIONAL basis the
    # REST door takes; omitted = last-write-wins exactly as before.
    basis: datetime | None = None
    if basis_updated_at:
        try:
            basis = datetime.fromisoformat(basis_updated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise invalid_input(f"basis_updated_at is not an ISO datetime: {exc}")
    async with get_db() as db:
        try:
            result = await profile_svc.patch_profile_section(
                section, data, db, provider=provider, basis_updated_at=basis
            )
        except StaleEditError as exc:
            raise invalid_input(str(exc))
        except ValueError as exc:
            raise invalid_input(str(exc))
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Submit facts elicited from the candidate as free-text testimony "
        "(their own words); Applire reconciles them into the profile with "
        "receipts. Recorded, not verified — the vault stays self-attested. "
        "Read resource schema://claims first (max 20/call). Optional `gap` = "
        "EXACT concept string from analyze_gaps (requires job_id). Ambiguous/"
        "conflicting claims are parked in the profile Health hub, reported "
        "per claim. Denials are recorded (denial_recorded), never dropped."
    )
)
async def submit_claims(claims: list[dict], job_id: str | None = None) -> dict:
    from pydantic import ValidationError

    from applire.schemas.claims import ClaimsSubmission
    from applire.services.profile.reconcile.agent_bridge import submit_agent_claims

    try:
        submission = ClaimsSubmission.model_validate({"claims": claims})
    except ValidationError as exc:
        raise invalid_input(str(exc))
    jid = _parse_uuid(job_id, "job_id") if job_id is not None else None
    provider = get_provider()
    async with get_db() as db:
        try:
            result = await submit_agent_claims(submission, jid, db, provider)
        except ValueError as exc:
            raise invalid_input(str(exc))
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Reconcile ONE whole free-text testimony document into the profile "
        "with receipts (itemized claims: use submit_claims instead). Read "
        "schema://testimony first."
    )
)
async def submit_testimony(text: str) -> dict:
    from pydantic import ValidationError

    from applire.schemas.testimony import TestimonyRequest
    from applire.services.profile.reconcile.testimony_bridge import (
        submit_testimony as submit_testimony_svc,
    )

    try:
        request = TestimonyRequest(text=text)
    except ValidationError as exc:
        raise invalid_input(str(exc))
    provider = get_provider()
    async with get_db() as db:
        try:
            result = await submit_testimony_svc(request.text, db, provider)
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(description="Analyse gaps between the current profile and the specified job.")
async def analyze_gaps(job_id: str) -> dict:
    jid = _parse_uuid(job_id, "job_id")
    provider = get_provider()
    async with get_db() as db:
        try:
            result = await gap_svc.analyze_gaps(jid, db, provider)
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Start a gap-fill interview session for the given job. "
        "Requires a gap analysis to exist (call analyze_gaps first). "
        "Returns session_id and the first question."
    )
)
async def run_interview(job_id: str) -> dict:
    jid = _parse_uuid(job_id, "job_id")
    provider = get_provider()
    async with get_db() as db:
        try:
            from applire.schemas.session import SessionCreateRequest as _SCR
            result = await session_svc.create_session(_SCR(job_id=jid), db, provider)
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Send a message in an active interview session. "
        "Returns the next question, or {complete: true} when finished. "
        "Reply 'done' to end. If 'pending_confirmations' is present, reply "
        "with one of the listed 'options' as the next message; never assume "
        "the answer (guide)."
    )
)
async def send_message(session_id: str, message: str) -> dict:
    sid = _parse_uuid(session_id, "session_id")
    if not message.strip():
        raise invalid_input("message must not be empty")
    provider = get_provider()
    async with get_db() as db:
        try:
            result = await session_svc.send_message(sid, message.strip(), db, provider)
        except LookupError as exc:
            raise not_found(str(exc))
        except ValueError as exc:
            raise invalid_input(str(exc))
        except LLMTruncatedError as exc:
            raise internal(
                f"{exc} The turn was rolled back — resend the same message to retry."
            )
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Resolve ONE gap cluster in a single call — the agent-channel form of "
        "the UI's targeted gap fill. Pass job_id, a gap_id from analyze_gaps' "
        "gap_clusters, and the candidate's own testimony. Returns {gap_id, "
        "question_asked, status, profile_completeness}. Stateless (see guide)."
    )
)
async def resolve_gap(job_id: str, gap_id: str, answer: str) -> dict:
    from applire.services.interview.signals import is_termination_signal

    jid = _parse_uuid(job_id, "job_id")
    gap_id = (gap_id or "").strip()
    answer = (answer or "").strip()
    if not gap_id:
        raise invalid_input("gap_id must not be empty")
    if not answer:
        raise invalid_input("answer must not be empty")
    # The answer is TESTIMONY, not a control word: a bare 'skip'/'done'/… would
    # be read as ending the session (and report a bogus 0.0 completeness). To
    # skip a gap, simply don't call resolve_gap for it.
    if is_termination_signal(answer):
        raise invalid_input(
            "answer must be the candidate's testimony about this gap, not a "
            "control word like 'skip'/'done'. To skip a gap, don't resolve it."
        )
    provider = get_provider()
    async with get_db() as db:
        valid_ids = await session_svc.gap_cluster_ids(jid, db)
        if valid_ids is None:
            raise not_found(
                "No gap analysis found for this job — call analyze_gaps first."
            )
        if not valid_ids:
            raise invalid_input(
                "This job's gap analysis has no gap clusters to resolve "
                "(near-complete match) — proceed to generate/render."
            )
        if gap_id not in valid_ids:
            raise invalid_input(
                f"Unknown gap_id {gap_id!r}. Valid gap cluster ids: "
                f"{', '.join(valid_ids)}"
            )
        # Don't stomp an in-progress full interview: _create_micro_session
        # completes any active session wholesale, silently discarding a guided
        # run's progress. A leftover 'targeted' micro-session is safe to reap.
        mode = await session_svc.active_session_mode(jid, db)
        if mode is not None and mode != "targeted":
            raise invalid_input(
                "A full interview is in progress for this job — finish it "
                "(reply 'done') before resolving gaps one at a time."
            )
        from applire.schemas.session import SessionCreateRequest as _SCR

        try:
            created = await session_svc.create_session(
                _SCR(job_id=jid, mode="targeted", target_gap=gap_id), db, provider
            )
            result = await session_svc.send_message(
                created.session_id, answer, db, provider
            )
        except LookupError as exc:
            raise not_found(str(exc))
        except ValueError as exc:
            raise invalid_input(str(exc))
        except LLMTruncatedError as exc:
            raise internal(
                f"{exc} The turn was rolled back (nothing saved) — call "
                "resolve_gap again with the same arguments to retry."
            )
        except Exception as exc:
            raise internal(str(exc))
    # A targeted micro-session always completes on the one answer; surface a
    # clean, honest status rather than the internal "max_questions_reached".
    #   needs_confirmation — the reconciler flagged an ambiguity it won't guess
    #     (the answer WAS applied, but a refinement is parked for the human);
    #   addressed — the testimony wrote a change into the vault;
    #   denial_recorded (#231) — the testimony explicitly denied a skill and
    #     nothing else changed; the denial IS recorded (metadata.denied_concepts
    #     + a receipt) so a later analyze_gaps run cannot re-infer it via
    #     adjacency — never silently "no_change";
    #   no_change — a valid answer that added nothing AND denied nothing.
    pending = [c.model_dump(mode="json") for c in (result.pending_confirmations or [])]
    conflicts = [c.model_dump(mode="json") for c in (result.pending_conflicts or [])]
    if pending or conflicts:
        status = "needs_confirmation"
    elif result.changes_applied:
        status = "addressed"
    elif result.denial_recorded:
        status = "denial_recorded"
    else:
        status = "no_change"
    out = {
        "gap_id": gap_id,
        "question_asked": created.first_question,
        "status": status,
        "profile_completeness": result.completeness_score,
    }
    if pending:
        out["pending_confirmations"] = pending
    if conflicts:
        out["pending_conflicts"] = conflicts
    return out


@mcp.tool(
    description=(
        "Generate a tailored CV for the given job. "
        "Returns cv_id, html_url, and pdf_url. "
        "The URLs point to the FastAPI backend (APPLIRE_BASE_URL). "
        "Optional target_pages pins the CV to a specific page count for this "
        f"generation only ({DEFAULT_REGION} norm: "
        f"{REGION_NORMS[DEFAULT_REGION].cv_standard_pages} pages standard, "
        f"{REGION_NORMS[DEFAULT_REGION].cv_max_pages} max); omit it to use "
        "the user's default setting, then the region standard."
    )
)
async def generate_cv(job_id: str, target_pages: int | None = None) -> dict:
    jid = _parse_uuid(job_id, "job_id")
    if target_pages is not None and not (1 <= target_pages <= MAX_TARGET_PAGES):
        raise invalid_input(f"target_pages must be between 1 and {MAX_TARGET_PAGES}")
    provider = get_provider()
    async with get_db() as db:
        try:
            result = await cv_svc.generate_cv(
                jid,
                db,
                provider,
                base_url=settings.applire_base_url,
                target_pages=target_pages,
            )
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Poll the status of a CV generation. "
        "Returns {cv_id, status, html_url?, pdf_url?, expires_at?}. "
        "status: 'pending' | 'generating' | 'ready' | 'failed'."
    )
)
async def get_cv_status(cv_id: str) -> dict:
    cid = _parse_uuid(cv_id, "cv_id")
    async with get_db() as db:
        try:
            result = await cv_svc.get_cv_status(cid, db, settings.applire_base_url)
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Get the persisted ATS audit report for a generated CV. Returns "
        "{document_id, status, report}; report is null while pending — named "
        "checks + keyword presence, no aggregate score."
    )
)
async def get_cv_ats_report(cv_id: str) -> dict:
    cid = _parse_uuid(cv_id, "cv_id")
    async with get_db() as db:
        try:
            result = await cv_svc.get_cv_ats_report(cid, db)
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


async def _audit_stored_document(record, kind: str, db) -> dict:
    """Persisted-or-fresh truthfulness report for a generated CV/letter row."""
    if record.truthfulness_report:
        return {"document_id": str(record.id), **record.truthfulness_report}
    profile = await db.get(MasterProfile, record.profile_id)
    profile_json = (profile.profile_json if profile else {}) or {}
    try:
        if kind == "cv":
            report = await oracle_svc.audit_document(
                "cv", profile_json, tailored_data=record.tailored_data or {},
                provider=get_provider(),
            )
        else:
            report = await oracle_svc.audit_document(
                "cover_letter", profile_json, letter_data=record.letter_data or {},
                provider=get_provider(),
            )
    except Exception as exc:
        raise internal(str(exc))
    record.truthfulness_report = report.model_dump(mode="json")
    await db.commit()
    return {"document_id": str(record.id), **record.truthfulness_report}


@mcp.tool(
    description=(
        "Audit a document against the vault: per-claim verdicts grounded | "
        "inflated | misattributed | unbacked | unverifiable, with evidence "
        "refs. Pass EXACTLY ONE of document_id (generated CV/letter — "
        "persisted report) or document_text (raw text; no position anchors, "
        "misattribution skipped). Verifies document-vault consistency only — "
        "the vault is self-attested."
    )
)
async def audit_document(
    document_id: str | None = None, document_text: str | None = None
) -> dict:
    if (document_id is None) == (document_text is None):
        raise invalid_input("Pass exactly one of document_id or document_text")
    async with get_db() as db:
        if document_text is not None:
            if not document_text.strip():
                raise invalid_input("document_text is empty")
            result = await db.execute(
                select(MasterProfile)
                .where(MasterProfile.deleted_at.is_(None))
                .order_by(MasterProfile.created_at.desc())
                .limit(1)
            )
            profile = result.scalar_one_or_none()
            if profile is None:
                raise not_found("No profile found — import a CV first")
            try:
                report = await oracle_svc.audit_document(
                    "external", profile.profile_json or {},
                    text=document_text, provider=get_provider(),
                )
            except Exception as exc:
                raise internal(str(exc))
            return report.model_dump(mode="json")

        did = _parse_uuid(document_id, "document_id")
        cv = await db.get(GeneratedCV, did)
        if cv is not None and cv.deleted_at is None:
            return await _audit_stored_document(cv, "cv", db)
        cl = await db.get(GeneratedCoverLetter, did)
        if cl is not None and cl.deleted_at is None:
            return await _audit_stored_document(cl, "cover_letter", db)
        raise not_found(f"No generated CV or cover letter with id {document_id}")


@mcp.tool(
    description=(
        "Render YOUR agent-authored structured content into a norms-checked, "
        "templated PDF — Applire renders, checks, reports; it NEVER rewrites "
        "your content. Read resource schema://cv or schema://cover-letter "
        "first; unknown fields are rejected with field paths. Returns "
        "document_id, pdf_url/html_url, schema_version, ATS + truthfulness "
        "reports. UI-visible only after create_application (guide)."
    )
)
async def render_document(
    document_kind: str,
    content: dict,
    job_id: str,
    template: str | None = None,
    target_pages: int | None = None,
) -> dict:
    from typing import get_args

    from pydantic import ValidationError

    from applire.schemas.cv import CV_SCHEMA_VERSION, CVTemplate
    from applire.schemas.cover_letter import LETTER_SCHEMA_VERSION

    if document_kind not in ("cv", "cover_letter"):
        raise invalid_input("document_kind must be 'cv' or 'cover_letter'")
    jid = _parse_uuid(job_id, "job_id")
    if not isinstance(content, dict) or not content:
        raise invalid_input(
            f"content must be a non-empty object matching resource schema://"
            f"{'cv' if document_kind == 'cv' else 'cover-letter'}"
        )
    tmpl = template or "classic_german"
    valid_templates = get_args(CVTemplate)
    if tmpl not in valid_templates:
        raise invalid_input(
            f"Unknown template {tmpl!r}. Valid templates: {', '.join(valid_templates)}"
        )
    if target_pages is not None:
        if document_kind == "cover_letter":
            raise invalid_input("target_pages only applies to document_kind='cv'")
        if not (1 <= target_pages <= MAX_TARGET_PAGES):
            raise invalid_input(f"target_pages must be between 1 and {MAX_TARGET_PAGES}")

    base = settings.applire_base_url
    async with get_db() as db:
        try:
            if document_kind == "cv":
                record = await cv_svc.render_agent_cv(
                    content, jid, db, template=tmpl, target_pages=target_pages
                )
                return {
                    "document_id": str(record.id),
                    "document_kind": "cv",
                    "status": record.status,
                    "schema_version": CV_SCHEMA_VERSION,
                    "html_url": f"{base}/api/cv/{record.id}/html",
                    "pdf_url": f"{base}/api/cv/{record.id}/pdf",
                    "expires_at": record.expires_at.isoformat(),
                    "ats_report": record.ats_report,
                    "truthfulness_report": record.truthfulness_report,
                }
            cl = await cover_letter_svc.render_agent_letter(
                content, jid, db, template=tmpl
            )
            return {
                "document_id": str(cl.id),
                "document_kind": "cover_letter",
                "status": cl.status,
                "schema_version": LETTER_SCHEMA_VERSION,
                "html_url": f"{base}/api/cover-letter/{cl.id}/html",
                "pdf_url": f"{base}/api/cover-letter/{cl.id}/pdf",
                "expires_at": cl.expires_at.isoformat(),
                "ats_report": cl.ats_report,
                "truthfulness_report": cl.truthfulness_report,
            }
        except LookupError as exc:
            raise not_found(str(exc))
        except (ValidationError, ValueError) as exc:
            # Pydantic errors carry agent-actionable field paths in str().
            raise invalid_input(str(exc))
        except Exception as exc:
            raise internal(str(exc))


@mcp.tool(
    description=(
        "Generate a cover letter for the given job. Requires an existing flow "
        "session (call start_flow first). Returns cover_letter_id, status, "
        "html_url, and pdf_url. Editing a generated letter's sections is "
        "UI-only."
    )
)
async def generate_cover_letter(job_id: str) -> dict:
    jid = _parse_uuid(job_id, "job_id")
    provider = get_provider()
    async with get_db() as db:
        try:
            result = await cover_letter_svc.generate_cover_letter(
                CoverLetterGenerateRequest(job_id=jid),
                db,
                provider,
                base_url=settings.applire_base_url,
            )
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Poll the status of a cover letter generation. "
        "Returns {cover_letter_id, status, html_url?, pdf_url?, expires_at?}. "
        "status: 'pending' | 'generating' | 'ready' | 'failed'."
    )
)
async def get_cover_letter_status(cover_letter_id: str) -> dict:
    cid = _parse_uuid(cover_letter_id, "cover_letter_id")
    async with get_db() as db:
        try:
            result = await cover_letter_svc.get_cover_letter_status(
                cid, db, settings.applire_base_url
            )
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Get the persisted ATS audit report for a generated cover letter. "
        "Returns {document_id, status, report}; report is null while pending "
        "— named checks + keyword presence, no aggregate score."
    )
)
async def get_cover_letter_ats_report(cover_letter_id: str) -> dict:
    cid = _parse_uuid(cover_letter_id, "cover_letter_id")
    async with get_db() as db:
        try:
            result = await cover_letter_svc.get_cover_letter_ats_report(cid, db)
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Create or resume a flow session. Pass job_id to bind the flow to a job "
        "(idempotent per user+job); omit it for a CV-only flow. Returns flow_id + state."
    )
)
async def start_flow(job_id: str | None = None) -> dict:
    jid = _parse_uuid(job_id, "job_id") if job_id else None
    async with get_db() as db:
        # _current_user_id raises McpError directly; keep it outside the try so a
        # missing user stays -32001 NotFound rather than being remapped to -32603.
        uid = await _current_user_id(db)
        try:
            result = await flow_svc.create_flow(
                CreateFlowRequest(job_id=jid), uid, db, settings.applire_base_url
            )
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Advance a flow to the next step. Steps that produce an artifact require "
        "artifact_id (gap_analysis→gap_analysis_id, interview→interview_session_id, "
        "complete→generated_cv_id). flow_id is the stable handle for session recovery."
    )
)
async def advance_flow(flow_id: str, step: str, artifact_id: str | None = None) -> dict:
    fid = _parse_uuid(flow_id, "flow_id")
    aid = _parse_uuid(artifact_id, "artifact_id") if artifact_id else None
    async with get_db() as db:
        try:
            result = await flow_svc.advance_flow(
                fid, AdvanceFlowRequest(step=step, artifact_id=aid), db, settings.applire_base_url
            )
        except (InvalidTransitionError, ArtifactRequiredError) as exc:
            raise invalid_input(str(exc))
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(description="Get the current state of a flow session, including available actions.")
async def get_flow_state(flow_id: str) -> dict:
    fid = _parse_uuid(flow_id, "flow_id")
    async with get_db() as db:
        try:
            result = await flow_svc.get_flow_state(fid, db, settings.applire_base_url)
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


# Valid user_status values, derived from the enum so tool descriptions and
# error messages can never go stale again (the old literal lacked 'hired').
_USER_STATUS_VALUES = ", ".join(m.value for m in UserStatus)


def _parse_user_status(raw: str, field: str) -> UserStatus:
    try:
        return UserStatus(raw)
    except ValueError:
        raise invalid_input(
            f"Invalid {field}: {raw!r}. Must be one of: {_USER_STATUS_VALUES}."
        )


@mcp.tool(
    description=(
        "List the user's application pipeline. "
        f"Optional status_filter: {_USER_STATUS_VALUES}."
    )
)
async def list_applications(status_filter: str | None = None) -> list[dict]:
    user_status = None
    if status_filter:
        user_status = _parse_user_status(status_filter, "status_filter")
    # Retrieve the single user from the DB (MCP runs in single-user context).
    async with get_db() as db:
        user_result = await db.execute(select(User).limit(1))
        user = user_result.scalar_one_or_none()
        if user is None:
            raise not_found("No user found — create a user first")
        try:
            result = await app_svc.list_applications(
                user_id=user.id,
                db=db,
                workflow_status=None,
                user_status=user_status,
            )
        except Exception as exc:
            raise internal(str(exc))
    return [item.model_dump(mode="json") for item in result.items]


@mcp.tool(
    description=(
        "Get details for a specific application by ID. A non-null stale_cv "
        "field means the profile grew after the newest CV was tailored — "
        "offer to re-tailor via generate_cv, or mute with "
        "update_application(dismiss_stale_cv=true). Never regenerate without "
        "asking; a pinned submitted version is never replaced."
    )
)
async def get_application(application_id: str) -> dict:
    aid = _parse_uuid(application_id, "application_id")
    async with get_db() as db:
        uid = await _current_user_id(db)
        try:
            result = await app_svc.get_application(aid, uid, db)
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Log an application to the user's pipeline. job_id is the JobAnalysis "
        "id; company_name/role_title/source_url default from the job when "
        "omitted. start_workflow=true atomically creates the flow session."
    )
)
async def create_application(
    job_id: str,
    start_workflow: bool = False,
    company_name: str | None = None,
    role_title: str | None = None,
    deadline: str | None = None,
    source_url: str | None = None,
) -> dict:
    jid = _parse_uuid(job_id, "job_id")
    dl = None
    if deadline:
        try:
            dl = datetime.fromisoformat(deadline)
        except ValueError:
            raise invalid_input("deadline must be ISO 8601 (e.g. 2026-07-01T00:00:00)")
    req = CreateApplicationRequest(
        job_analysis_id=jid,
        start_workflow=start_workflow,
        company_name=company_name,
        role_title=role_title,
        deadline=dl,
        source_url=source_url,
    )
    async with get_db() as db:
        uid = await _current_user_id(db)
        try:
            result = await app_svc.create_application(uid, req, db)
        except app_svc.ConflictError as exc:
            raise invalid_input(str(exc))
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Update user-managed fields (omitted ones stay unchanged). "
        f"user_status: one of {_USER_STATUS_VALUES}. deadline: ISO 8601. "
        "submitted_cv_id/submitted_cover_letter_id: the sent document. "
        "dismiss_stale_cv=true mutes the stale hint. language_override: "
        "'de'/'en'/'auto'. add_fact_pin {entry_type, entry_id, quote, "
        "targets?} pins a verbatim vault quote generation must keep; "
        "remove_fact_pin: pin_id — see guide."
    )
)
async def update_application(
    application_id: str,
    user_status: str | None = None,
    company_name: str | None = None,
    role_title: str | None = None,
    notes: str | None = None,
    deadline: str | None = None,
    source_url: str | None = None,
    submitted_cv_id: str | None = None,
    submitted_cover_letter_id: str | None = None,
    dismiss_stale_cv: bool | None = None,
    language_override: str | None = None,
    add_fact_pin: dict | None = None,
    remove_fact_pin: str | None = None,
) -> dict:
    aid = _parse_uuid(application_id, "application_id")
    # Build the request from provided fields only, so PatchApplicationRequest's
    # model_fields_set semantics stay honest (E039: omitted ≠ explicit null).
    fields: dict = {}
    if user_status is not None:
        fields["user_status"] = _parse_user_status(user_status, "user_status")
    if company_name is not None:
        fields["company_name"] = company_name
    if role_title is not None:
        fields["role_title"] = role_title
    if notes is not None:
        fields["notes"] = notes
    if deadline is not None:
        try:
            fields["deadline"] = datetime.fromisoformat(deadline)
        except ValueError:
            raise invalid_input("deadline must be ISO 8601 (e.g. 2026-07-01T00:00:00)")
    if source_url is not None:
        fields["source_url"] = source_url
    if submitted_cv_id is not None:
        fields["submitted_cv_id"] = _parse_uuid(submitted_cv_id, "submitted_cv_id")
    if submitted_cover_letter_id is not None:
        fields["submitted_cover_letter_id"] = _parse_uuid(
            submitted_cover_letter_id, "submitted_cover_letter_id"
        )
    if dismiss_stale_cv is not None:
        fields["dismiss_stale_cv"] = dismiss_stale_cv
    if language_override is not None:
        # E054 / ADR-038 amendment clause 5: this tool builds its request by
        # skipping None, so an explicit null can never reach the clearable
        # PATCH semantics from this channel — 'auto' is the clear sentinel.
        if language_override == "auto":
            fields["language_override"] = None
        elif language_override in ("de", "en"):
            fields["language_override"] = language_override
        else:
            raise invalid_input("language_override must be 'de', 'en' or 'auto'")
    # ADR-077 clause 6 — fact-pin ops share this tool (hard ADR-058 parity
    # with the REST pins subresource: additive add, idempotent remove).
    pin_request: AddFactPinRequest | None = None
    if add_fact_pin is not None:
        try:
            pin_request = AddFactPinRequest.model_validate(add_fact_pin)
        except Exception as exc:
            raise invalid_input(f"add_fact_pin: {exc}")
    if not fields and pin_request is None and remove_fact_pin is None:
        raise invalid_input(
            "At least one field must be provided (user_status, company_name, "
            "role_title, notes, deadline, source_url, submitted_cv_id, "
            "submitted_cover_letter_id, dismiss_stale_cv, language_override, "
            "add_fact_pin, remove_fact_pin)."
        )
    async with get_db() as db:
        uid = await _current_user_id(db)
        try:
            if pin_request is not None:
                await pin_svc.add_fact_pin(aid, uid, pin_request, db)
            if remove_fact_pin is not None:
                await pin_svc.remove_fact_pin(aid, uid, remove_fact_pin, db)
            if fields:
                req = PatchApplicationRequest(**fields)
                result = await app_svc.patch_application(aid, uid, req, db)
            else:
                result = await app_svc.get_application(aid, uid, db)
        except LookupError as exc:
            raise not_found(str(exc))
        except ValueError as exc:
            raise invalid_input(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Add a new ongoing role to the Master Profile (post-hire update). "
        "close_role_ids lists prior open roles to close; each is closed the day "
        "before start_date. Dates are YYYY-MM-DD."
    )
)
async def add_role(
    title: str,
    company: str,
    start_date: str,
    location: str | None = None,
    industry: str | None = None,
    close_role_ids: list[str] | None = None,
) -> dict:
    try:
        start = date.fromisoformat(start_date)
    except ValueError:
        raise invalid_input("start_date must be YYYY-MM-DD")
    close_end = (start - timedelta(days=1)).isoformat()
    close_roles = [CloseRoleEntry(role_id=rid, end_date=close_end) for rid in (close_role_ids or [])]
    req = AddRoleRequest(
        title=title, company=company, start_date=start_date,
        location=location, industry=industry, close_roles=close_roles, source="manual",
    )
    async with get_db() as db:
        try:
            result = await add_role_to_profile(req, db)
        except AddRoleValidationError as exc:
            raise invalid_input(str(exc))
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Resources (7.9 – 7.11)
# ---------------------------------------------------------------------------


@mcp.resource(
    "profile://current",
    mime_type="application/json",
    description="Current MasterProfile JSON.",
)
async def resource_profile() -> str:
    async with get_db() as db:
        result = await profile_svc.get_profile(db)
    if result is None:
        raise not_found("No profile found")
    return json.dumps(result.model_dump(mode="json"))


@mcp.resource(
    "schema://cv",
    mime_type="application/json",
    description=(
        "The public versioned tailored-CV content contract (ADR-054): "
        "{schema_version, json_schema}. Read this before calling "
        "render_document(document_kind='cv')."
    ),
)
async def resource_schema_cv() -> str:
    from applire.schemas.cv import CV_SCHEMA_VERSION, TailoredCVData

    return json.dumps(
        {
            "schema_version": CV_SCHEMA_VERSION,
            "json_schema": TailoredCVData.model_json_schema(),
        }
    )


@mcp.resource(
    "schema://cover-letter",
    mime_type="application/json",
    description=(
        "The public versioned cover-letter content contract (ADR-054): "
        "{schema_version, json_schema}. Read this before calling "
        "render_document(document_kind='cover_letter')."
    ),
)
async def resource_schema_cover_letter() -> str:
    from applire.schemas.cover_letter import LETTER_SCHEMA_VERSION, LetterData

    return json.dumps(
        {
            "schema_version": LETTER_SCHEMA_VERSION,
            "json_schema": LetterData.model_json_schema(),
        }
    )


@mcp.resource(
    "guide://usage",
    mime_type="text/markdown",
    description=(
        "The Applire agent-usage guide + honesty contract (ADR-056). "
        "Same content as the get_guide tool."
    ),
)
async def resource_guide_usage() -> str:
    return _load_guide()


@mcp.resource(
    "schema://claims",
    mime_type="application/json",
    description=(
        "The public versioned agent-testimony contract (ADR-054, E045): "
        "{schema_version, json_schema}. Read this before calling "
        "submit_claims."
    ),
)
async def resource_schema_claims() -> str:
    from applire.schemas.claims import CLAIMS_SCHEMA_VERSION, ClaimsSubmission

    return json.dumps(
        {
            "schema_version": CLAIMS_SCHEMA_VERSION,
            "json_schema": ClaimsSubmission.model_json_schema(),
        }
    )


@mcp.resource(
    "schema://testimony",
    mime_type="application/json",
    description=(
        "The public versioned free-text testimony contract (#258): "
        "{schema_version, json_schema}. Read this before calling "
        "submit_testimony."
    ),
)
async def resource_schema_testimony() -> str:
    from applire.schemas.testimony import TESTIMONY_SCHEMA_VERSION, TestimonyRequest

    return json.dumps(
        {
            "schema_version": TESTIMONY_SCHEMA_VERSION,
            "json_schema": TestimonyRequest.model_json_schema(),
        }
    )


@mcp.resource(
    "job://{job_id}",
    mime_type="application/json",
    description="JobAnalysis JSON for the given job_id.",
)
async def resource_job(job_id: str) -> str:
    jid = _parse_uuid(job_id, "job_id")
    async with get_db() as db:
        result = await db.execute(
            select(JobAnalysis).where(
                JobAnalysis.id == jid,
                JobAnalysis.deleted_at.is_(None),
            )
        )
        record = result.scalar_one_or_none()
    if record is None:
        raise not_found(f"Job analysis {job_id} not found")
    return json.dumps(JobAnalysisResponse.model_validate(record).model_dump(mode="json"))


@mcp.resource(
    "flow://{flow_id}",
    mime_type="application/json",
    description="FlowStateResponse JSON for the given flow_id.",
)
async def resource_flow(flow_id: str) -> str:
    fid = _parse_uuid(flow_id, "flow_id")
    async with get_db() as db:
        try:
            result = await flow_svc.get_flow_state(fid, db, settings.applire_base_url)
        except LookupError as exc:
            raise not_found(str(exc))
    return json.dumps(result.model_dump(mode="json"))


@mcp.resource(
    "cv://{cv_id}",
    mime_type="application/json",
    description="GeneratedCV metadata JSON for the given cv_id.",
)
async def resource_cv(cv_id: str) -> str:
    cid = _parse_uuid(cv_id, "cv_id")
    async with get_db() as db:
        result = await db.execute(
            select(GeneratedCV).where(
                GeneratedCV.id == cid,
                GeneratedCV.deleted_at.is_(None),
            )
        )
        record = result.scalar_one_or_none()
    if record is None:
        raise not_found(f"Generated CV {cv_id} not found")
    return json.dumps(GeneratedCVResponse.model_validate(record).model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Prompts (ADR-056)
# ---------------------------------------------------------------------------


@mcp.prompt(
    name="how-to-use-applire",
    description=(
        "How to drive Applire well over MCP — tool flow, path choice, and "
        "the honesty contract (same content as the get_guide tool)."
    ),
)
async def prompt_how_to_use_applire() -> str:
    return _load_guide()
