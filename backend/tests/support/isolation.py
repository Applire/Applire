# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``RESOURCE_FACTORIES`` — the cross-user isolation suite's registry (ADR-092
cl. 8c; US332; Strawberry package 3a owns this module).

Every id a door accepts — a REST path parameter, an MCP tool id argument, an MCP
resource-template variable — must name either

* a **factory** in ``RESOURCE_FACTORIES``: ``async (OwnerWorld) -> str`` builds the
  resource for that world's owner (user A) and returns its id; or
* a **sub-key** in ``NOT_RESOURCE_PARAMS``: it addresses something *inside* a
  resource another parameter already names (a CV section, a gap cluster) or the
  caller's own vault, with the reason.

An unmapped id fails ``tests/unit/test_cross_user_isolation.py`` — a new door
cannot slip past the suite. Keys are ``"<param>"`` or ``"<param>@<path prefix>"``
when one name means different resources under different prefixes (``doc_id``);
the longest matching prefix wins.

Other packages add factories in their own ``tests/support/owners_<pkg>.py`` by
calling ``register(...)``; ``load_package_factories()`` imports every such module.
"""

from __future__ import annotations

import importlib
import pkgutil
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import tests.support as _support_pkg
from applire.models.application import Application
from applire.models.cover_letter import GeneratedCoverLetter
from applire.models.cv import GeneratedCV
from applire.models.flow import FlowSession
from applire.models.gap import GapAnalysis
from applire.models.gap_job import GapAnalysisJob
from applire.models.import_job import CVImportJob
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile, authorized_profile_write
from applire.models.session import InterviewSession
from applire.models.uploads import UploadRecord
from applire.models.user import User


def _interview_state(profile_id: uuid.UUID, job_id: uuid.UUID | None) -> dict:
    """The minimal interview-state keys the session/enrich routes read."""
    return {
        "profile_id": str(profile_id),
        "job_id": str(job_id) if job_id else None,
        "messages": [],
        "critical_gaps": ["Kubernetes"],
        "current_gap_index": 0,
        "current_question": "Tell me about Kubernetes.",
        "current_choices": [],
        "questions_asked": 1,
        "questions_per_gap": {},
        "addressed_gaps": [],
        "gap_clusters_by_id": {},
        "hard_ceiling": 12,
    }


class OwnerWorld:
    """One owner's resources, built lazily and shared between factories (one live
    profile per owner, ADR-092 cl. 2). Flushes; the caller commits."""

    def __init__(self, db: Any, user: User) -> None:
        self.db = db
        self.user = user
        self._cache: dict[str, Any] = {}

    async def _once(self, key: str, build: Callable[[], Awaitable[Any]]) -> Any:
        if key not in self._cache:
            self._cache[key] = await build()
            await self.db.flush()
        return self._cache[key]

    async def profile(self) -> MasterProfile:
        async def build():
            with authorized_profile_write():
                p = MasterProfile(
                    user_id=self.user.id,
                    profile_json={"personal_info": {"name": f"Owner {self.user.email}"}},
                )
            self.db.add(p)
            return p

        return await self._once("profile", build)

    async def job(self) -> JobAnalysis:
        """A shared posting plus this owner's link to it (RD-2: the application)."""

        async def build():
            j = JobAnalysis(
                raw_text_hash=uuid.uuid4().hex, raw_text="Engineer, Python.",
                role_title="Engineer", seniority_level="mid", language_requirement="English",
            )
            self.db.add(j)
            return j

        return await self._once("job", build)

    async def application(self) -> Application:
        async def build():
            job = await self.job()
            a = Application(
                user_id=self.user.id, job_analysis_id=job.id,
                company_name="Acme", role_title="Engineer",
            )
            self.db.add(a)
            return a

        return await self._once("application", build)

    async def flow(self) -> FlowSession:
        async def build():
            app = await self.application()
            f = FlowSession(user_id=self.user.id, job_id=app.job_analysis_id, application_id=app.id)
            self.db.add(f)
            await self.db.flush()
            app.flow_session_id = f.id
            return f

        return await self._once("flow", build)

    async def cv(self) -> GeneratedCV:
        async def build():
            await self.application()
            c = GeneratedCV(
                job_analysis_id=(await self.job()).id, profile_id=(await self.profile()).id,
                user_id=self.user.id, tailored_data={}, status="ready",
                content_snapshot={
                    "introduction": "Engineer with Python.", "positions": [], "skills": ["Python"],
                },
            )
            self.db.add(c)
            return c

        return await self._once("cv", build)

    async def cover_letter(self) -> GeneratedCoverLetter:
        async def build():
            await self.application()
            c = GeneratedCoverLetter(
                job_analysis_id=(await self.job()).id, profile_id=(await self.profile()).id,
                user_id=self.user.id, letter_data={}, status="ready",
            )
            self.db.add(c)
            return c

        return await self._once("cover_letter", build)

    async def gap_analysis(self) -> GapAnalysis:
        async def build():
            await self.application()
            g = GapAnalysis(
                job_analysis_id=(await self.job()).id, profile_id=(await self.profile()).id,
                user_id=self.user.id, match_score=0.5,
                gap_clusters=[{
                    "id": "cluster-1", "label": "Cloud", "category": "C",
                    "gaps": ["Kubernetes"], "jd_skills": ["Kubernetes"], "jd_context": "",
                }],
            )
            self.db.add(g)
            return g

        return await self._once("gap_analysis", build)

    async def interview(self) -> InterviewSession:
        async def build():
            await self.application()
            job, profile = await self.job(), await self.profile()
            s = InterviewSession(
                job_analysis_id=job.id, profile_id=profile.id, user_id=self.user.id,
                state=_interview_state(profile.id, job.id),
                status="active",
            )
            self.db.add(s)
            return s

        return await self._once("interview", build)

    async def enrich_session(self) -> InterviewSession:
        """A profile-enrichment session (``routers/profile_enrich.py``, no job)."""

        async def build():
            profile = await self.profile()
            s = InterviewSession(
                job_analysis_id=None, profile_id=profile.id, user_id=self.user.id,
                mode="profile_enrich", status="active",
                state=_interview_state(profile.id, None),
            )
            self.db.add(s)
            return s

        return await self._once("enrich_session", build)

    async def gap_job(self) -> GapAnalysisJob:
        async def build():
            await self.application()
            g = GapAnalysisJob(job_analysis_id=(await self.job()).id, user_id=self.user.id, status="pending")
            self.db.add(g)
            return g

        return await self._once("gap_job", build)

    async def import_job(self) -> CVImportJob:
        async def build():
            j = CVImportJob(user_id=self.user.id, filename="cv.pdf")
            self.db.add(j)
            return j

        return await self._once("import_job", build)

    async def staged_upload(self) -> UploadRecord:
        async def build():
            u = UploadRecord(
                user_id=self.user.id, original_filename="cv.pdf", content_hash="0" * 64,
                mime_type="application/pdf", file_path="/nonexistent/cv.pdf", byte_size=1,
                gate_status="name_divergence",
                staged_extraction={"personal_info": {"name": "Somebody Else"}},
            )
            self.db.add(u)
            return u

        return await self._once("staged_upload", build)


async def _linked_job(world: OwnerWorld) -> str:
    """The posting with the owner's whole chain linked through the flow, so routes
    keyed on ``job_id`` (gaps, letter by job) find A's rows when A asks."""
    flow = await world.flow()
    flow.generated_cv_id = (await world.cv()).id
    flow.generated_cover_letter_id = (await world.cover_letter()).id
    flow.gap_analysis_id = (await world.gap_analysis()).id
    flow.interview_session_id = (await world.interview()).id
    await world.db.flush()
    return str((await world.job()).id)


Factory = Callable[[OwnerWorld], Awaitable[str]]

RESOURCE_FACTORIES: dict[str, Factory] = {}

#: Ids that are not resources of their own — with the reason the suite skips them.
NOT_RESOURCE_PARAMS: dict[str, str] = {
    "section": "a section of the CALLER's own vault (PATCH /api/profile/{section})",
    "section_id": "a section inside the CV the route's cv_id names",
    "cluster_id": "a gap cluster inside the gap analysis of the route's job_id",
    "gap_id": "a gap cluster inside the analysis of the tool's job_id",
    "conflict_id": "a conflict inside the CALLER's own vault",
    "pin_id": "a fact pin inside the application the route's application_id names",
    "scheme_id": "instance colour scheme, admin-only (MD-6) — not owned",
    # W1 integration (1b/1c routers mounted): account management is admin-only
    # (require_admin; a non-admin B gets 403 before any lookup) — accounts and
    # instance probe tokens are not ADR-092 owned resources.
    "user_id@/api/admin/users": "an account, admin-only account management (ADR-091 cl. 22)",
    "token_id@/api/admin/probe-tokens": "an instance probe token, admin-only (ADR-091 cl. 17)",
}


def not_resource(param: str, path: str = "") -> bool:
    """``param`` is a declared sub-key / non-resource — bare, or ``param@<path prefix>``."""
    if param in NOT_RESOURCE_PARAMS:
        return True
    return any(
        k.startswith(f"{param}@") and path.startswith(k.split("@", 1)[1])
        for k in NOT_RESOURCE_PARAMS
    )


def register(key: str, factory: Factory) -> None:
    if key in RESOURCE_FACTORIES:
        raise ValueError(f"isolation factory {key!r} registered twice")
    RESOURCE_FACTORIES[key] = factory


def _id(attr: str) -> Factory:
    async def factory(world: OwnerWorld) -> str:
        return str((await getattr(world, attr)()).id)

    factory.__name__ = f"factory_{attr}"
    return factory


for _key, _attr in {
    "application_id": "application",
    "submitted_cv_id": "cv",
    "submitted_cover_letter_id": "cover_letter",
    "flow_id": "flow",
    "cv_id": "cv",
    "artifact_id": "cv",
    "document_id": "cv",
    "doc_id@/api/cv": "cv",
    "cl_id": "cover_letter",
    "cover_letter_id": "cover_letter",
    "doc_id@/api/cover-letter": "cover_letter",
    "session_id": "interview",
    "session_id@/api/profile/enrich": "enrich_session",
    "gap_job_id": "gap_job",
    "import_id": "import_job",
    "staged_id": "staged_upload",
}.items():
    register(_key, _id(_attr))
register("job_id", _linked_job)


def resolve(param: str, path: str = "") -> str | None:
    """The registry key for ``param`` under ``path`` (longest prefix first), else None."""
    prefixed = [
        k for k in RESOURCE_FACTORIES
        if k.startswith(f"{param}@") and path.startswith(k.split("@", 1)[1])
    ]
    if prefixed:
        return max(prefixed, key=len)
    return param if param in RESOURCE_FACTORIES else None


def load_package_factories() -> list[str]:
    """Import every ``tests.support.owners_<pkg>`` module (their ``register`` calls)."""
    loaded = []
    for mod in pkgutil.iter_modules(_support_pkg.__path__):
        if mod.name.startswith("owners_"):
            importlib.import_module(f"tests.support.{mod.name}")
            loaded.append(mod.name)
    return loaded
