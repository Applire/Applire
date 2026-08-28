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

"""#480 PR 8 — the three first-profile-creation sites, door by door.

The grep for `profile_json =` never saw these three: they create the row with a
KEYWORD ARGUMENT (`MasterProfile(profile_json=…)`), which is why the clause-6
guard had to fire on construction too and why the writer count came out 19
instead of 16. Routing them is PR 9's hard prerequisite — a strict guard with
these three still unrouted would refuse to let anyone create a profile at all.

The three doors:

* `_import_from_text` — first CV paste / LinkedIn / MCP `import_cv`;
* `_apply_merge`      — first browser `/upload`;
* `_create_guided_session` — the Mode-B stub, a vault that starts EMPTY.

Two different acts, deliberately kept different. The two imports have a profile
to write, so they go through `commit_ops` and inherit its whole invariant set on
the very first write. The guided stub has nothing to write: it needs a row to
hang a session's `profile_id` on, and `{}` is what it has always stored and what
its readers (`profile_json or {}`) index. It therefore uses the committer's
creation PRIMITIVE — same module, same write token, no invented content, and no
enrichment record claiming something happened to a vault that is still empty.

Same durability method as PR 2's file: a file-backed database, re-read over a
SEPARATE connection, so an uncommitted write is invisible.
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


@pytest_asyncio.fixture
async def durable_db(tmp_path):
    """A file-backed database — so "did the new row survive the request?" is a
    real question and not an identity-map artefact."""
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    from applire.db.session import Base

    url = f"sqlite+aiosqlite:///{tmp_path / 'vault.sqlite'}"
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


async def _read_back_the_only_profile(engine) -> dict:
    """A brand-new session on a brand-new connection: only COMMITTED state."""
    from applire.models.profile import MasterProfile

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = (await session.execute(select(MasterProfile))).scalars().all()
        assert len(rows) == 1, f"expected exactly one profile row, got {len(rows)}"
        return dict(rows[0].profile_json)


async def _snapshot_count(engine) -> int:
    from applire.models.profile import ProfileSnapshot

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        return len((await session.execute(select(ProfileSnapshot))).scalars().all())


# ── Door 1: the first CV paste / LinkedIn / MCP import ────────────────────────


def _extraction() -> dict:
    return {
        "personal_info": {"name": "Daniel Kovač", "email": "daniel@example.invalid"},
        "work_experience": [
            {
                "company": "Rheinwerk GmbH",
                "role": "Automation Engineer",
                "start_date": "2018-01",
                "end_date": "2023-12",
            }
        ],
        "skills": [{"name": "Kubernetes", "category": "technical"}],
    }


def _import_patches():
    extracted = _extraction()
    return (
        patch(
            "applire.services.profile.extract_with_fallback",
            new=AsyncMock(return_value=extracted),
        ),
        patch(
            "applire.services.profile.review_and_refine",
            new=AsyncMock(return_value=extracted),
        ),
        patch("applire.services.profile.annotate_expected_fields", new=AsyncMock()),
        patch(
            "applire.services.profile.enrich_skills",
            new=AsyncMock(side_effect=lambda p, _prov: p),
        ),
    )


@pytest.mark.asyncio
async def test_first_import_creates_a_profile_that_survives_the_request(durable_db):
    from applire.services.profile import import_from_text

    engine, factory = durable_db

    async with factory() as request_session:
        a, b, c, d = _import_patches()
        with a, b, c, d:
            await import_from_text("Kubernetes, five years.", request_session, AsyncMock())

    stored = await _read_back_the_only_profile(engine)
    assert [s["name"] for s in stored["skills"]] == ["Kubernetes"]
    assert stored["personal_info"]["name"] == "Daniel Kovač"


@pytest.mark.asyncio
async def test_first_import_carries_the_committers_invariants(durable_db):
    """A first import is a WRITE, so it inherits the invariant set from its very
    first byte: one unconditional trail entry carrying the same "initial import"
    receipt the hand-rolled record carried, the completeness recompute, and both
    clocks — with `created_via`/`created_at`, which only the intake can know,
    still supplied by the door."""
    from applire.services.profile import import_from_text

    engine, factory = durable_db

    async with factory() as request_session:
        a, b, c, d = _import_patches()
        with a, b, c, d:
            await import_from_text("Kubernetes, five years.", request_session, AsyncMock())

    metadata = (await _read_back_the_only_profile(engine))["metadata"]
    history = metadata["enrichment_history"]
    assert len(history) == 1
    assert history[0]["source"] == "cv_paste"
    assert [
        (c["action"], c["new_value"]) for c in history[0]["changes"]
    ] == [("added", "initial import")]
    assert metadata["created_via"] == "cv_paste"
    assert metadata["created_at"] is not None
    assert metadata["last_updated"] is not None
    assert metadata["completeness_score"] > 0


@pytest.mark.asyncio
async def test_first_import_creation_still_snapshots_nothing(durable_db):
    """Behaviour bound: `snapshot` stays exactly as each path passes it today.
    A first import has no pre-state to restore, and captured none before PR 8 —
    an ADR-042 snapshot of the empty row would be a restore point to nowhere."""
    from applire.services.profile import import_from_text

    engine, factory = durable_db

    async with factory() as request_session:
        a, b, c, d = _import_patches()
        with a, b, c, d:
            await import_from_text("Kubernetes, five years.", request_session, AsyncMock())

    assert await _snapshot_count(engine) == 0


@pytest.mark.asyncio
async def test_first_import_creation_is_an_authorised_write(durable_db):
    """PR 9's prerequisite, stated as a property: creating the first profile
    trips the clause-6 guard zero times. The keyword-argument constructor fires
    the setter, so before PR 8 this door was one of the writers keeping the
    guard in warn mode. Strict since PR 9 — the door completing IS the property;
    an unauthorised constructor would raise `UnauthorizedProfileWriteError`."""
    from applire.services.profile import import_from_text

    engine, factory = durable_db

    async with factory() as request_session:
        a, b, c, d = _import_patches()
        with a, b, c, d:
            await import_from_text("Kubernetes, five years.", request_session, AsyncMock())

    assert [s["name"] for s in (await _read_back_the_only_profile(engine))["skills"]] == [
        "Kubernetes"
    ]


# ── Door 2: the first browser `/upload` ───────────────────────────────────────


@pytest.mark.asyncio
async def test_first_upload_creates_a_profile_that_survives_the_request(durable_db):
    from applire.providers.embedding.noop import NoopEmbeddingProvider
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile import _apply_merge

    engine, factory = durable_db

    async with factory() as request_session:
        outcome = await _apply_merge(
            request_session,
            MasterProfileData.model_validate(
                {"skills": [{"name": "Terraform", "category": "technical"}]}
            ),
            source="cv_upload",
            emb_provider=NoopEmbeddingProvider(),
            provider=AsyncMock(),
        )
    returned_id = outcome.profile_id
    completeness = outcome.completeness
    conflicts = outcome.conflicts
    enrichment_id = outcome.enrichment_id

    stored = await _read_back_the_only_profile(engine)
    assert [s["name"] for s in stored["skills"]] == ["Terraform"]
    assert conflicts == []
    assert completeness == stored["metadata"]["completeness_score"]
    # The id this door hands back NAMES the record it wrote — the same property
    # the merge branch has had since PR 2. The creation branch used to return a
    # `uuid4()` minted at the top of the function that matched nothing on disk.
    assert str(enrichment_id) == stored["metadata"]["enrichment_history"][-1]["id"]
    assert returned_id is not None


@pytest.mark.asyncio
async def test_first_upload_creation_is_an_authorised_write(durable_db):
    """Strict since PR 9: an unauthorised constructor raises, so the door
    completing is the property. The read-back keeps it non-vacuous."""
    from applire.providers.embedding.noop import NoopEmbeddingProvider
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile import _apply_merge

    engine, factory = durable_db

    async with factory() as request_session:
        await _apply_merge(
            request_session,
            MasterProfileData.model_validate(
                {"skills": [{"name": "Terraform", "category": "technical"}]}
            ),
            source="cv_upload",
            emb_provider=NoopEmbeddingProvider(),
            provider=AsyncMock(),
        )

    assert [s["name"] for s in (await _read_back_the_only_profile(engine))["skills"]] == [
        "Terraform"
    ]


# ── Door 3: the Mode-B guided-interview stub ──────────────────────────────────


def _make_job():
    from applire.models.job import JobAnalysis

    return JobAnalysis(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Senior Python Engineer requiring Kafka and FastAPI.",
        role_title="Senior Python Engineer",
        required_skills=["Python", "Kafka", "FastAPI"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="English",
    )


async def _run_guided_session(factory):
    from applire.services.session import _create_guided_session

    async with factory() as session:
        job = _make_job()
        session.add(job)
        await session.commit()
        job_id = job.id

    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="(unused)")
    provider.aparse_json = AsyncMock(return_value={})
    async with factory() as request_session:
        job = (
            await request_session.execute(
                select(type(job)).where(type(job).id == job_id)
            )
        ).scalar_one()
        with patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(
                return_value={"question": "What do you do?", "choices": None}
            ),
        ):
            return await _create_guided_session(
                job_id, job, None, request_session, provider, "en"
            )


@pytest.mark.asyncio
async def test_guided_stub_creation_survives_the_request(durable_db):
    """MODE B starts from nothing: the stub row is what the session's
    `profile_id` points at, and if it does not survive the request the whole
    from-scratch interview has no vault to write into."""
    engine, factory = durable_db

    response = await _run_guided_session(factory)

    assert response.mode == "guided"
    assert await _read_back_the_only_profile(engine) == {}


@pytest.mark.asyncio
async def test_guided_stub_semantics_are_preserved_exactly(durable_db):
    """The stub's payload is `{}` — bit for bit, as it has always been.

    Nothing has happened to this vault yet, so routing the constructor must not
    invent a state for it: no metadata block, no completeness score, and above
    all no enrichment record claiming a change. The row exists so a session has
    something to point at; the first interview turn is the first real write, and
    THAT goes through `commit_ops` like every other turn.
    """
    engine, factory = durable_db

    await _run_guided_session(factory)

    assert await _read_back_the_only_profile(engine) == {}


@pytest.mark.asyncio
async def test_guided_stub_creation_is_an_authorised_write(durable_db):
    """The third of the three keyword-argument constructors, and the reason a
    strict guard would have broken Mode B outright. Strict since PR 9: the
    session completing is the property."""
    engine, factory = durable_db

    await _run_guided_session(factory)

    assert await _read_back_the_only_profile(engine) == {}
