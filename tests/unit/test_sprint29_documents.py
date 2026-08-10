"""Sprint 29 — My Documents backend (unit tests)

Run:
    pytest tests/unit/test_sprint29_documents.py -v
"""
import uuid
from datetime import datetime, timezone

from tests.support.profile_factory import make_master_profile

# ---------------------------------------------------------------------------
# Task 1 — Schema
# ---------------------------------------------------------------------------

def test_document_item_schema():
    """DocumentItem validates correctly and accepts None flow_id."""
    from applire.schemas.documents import DocumentItem
    from applire.models.cv import CVGenerationStatus

    now = datetime.now(timezone.utc)
    item = DocumentItem(
        cv_id=uuid.uuid4(),
        flow_id=None,
        role_title="Senior Engineer",
        company_name="Roche",
        template="classic_german",
        status=CVGenerationStatus.ready,
        created_at=now,
        expires_at=now,
    )
    assert item.role_title == "Senior Engineer"
    assert item.flow_id is None


def test_document_list_response_schema():
    """DocumentListResponse wraps items and total."""
    from applire.schemas.documents import DocumentItem, DocumentListResponse
    from applire.models.cv import CVGenerationStatus

    now = datetime.now(timezone.utc)
    item = DocumentItem(
        cv_id=uuid.uuid4(),
        flow_id=uuid.uuid4(),
        role_title="QA Lead",
        company_name="Bayer",
        template="modern_swiss",
        status=CVGenerationStatus.ready,
        created_at=now,
        expires_at=now,
    )
    resp = DocumentListResponse(items=[item], total=1)
    assert resp.total == 1
    assert len(resp.items) == 1


# ---------------------------------------------------------------------------
# Task 2 — Service
# ---------------------------------------------------------------------------
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_USER_A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_USER_B = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    # Import all models so Base knows about them
    import applire.models.user  # noqa
    import applire.models.profile  # noqa
    import applire.models.application  # noqa
    import applire.models.cv  # noqa
    import applire.models.job  # noqa
    import applire.models.flow  # noqa
    import applire.models.gap  # noqa
    import applire.models.session  # noqa
    import applire.models.cover_letter  # noqa
    import applire.models.color_profile  # noqa
    import applire.models.color_scheme  # noqa
    import applire.models.company  # noqa
    import applire.models.uploads  # noqa
    import applire.models.user_settings  # noqa

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


async def _seed_cv(db, user_id, role_title, company_name, template="classic_german", status="ready"):
    """Helper: seed the minimum rows needed for list_documents to return a result."""
    from applire.models.user import User
    from applire.models.profile import MasterProfile
    from applire.models.job import JobAnalysis
    from applire.models.application import Application, WorkflowStatus, UserStatus
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.schemas.profile import MasterProfileData, PersonalInfo
    from datetime import timedelta
    import hashlib

    now = datetime.now(timezone.utc)

    user = await db.get(User, user_id)
    if user is None:
        user = User(id=user_id, email=f"{user_id}@test.de")
        db.add(user)
        await db.flush()

    profile = make_master_profile(
        profile_json=MasterProfileData(
            personal_info=PersonalInfo(name="Test User")
        ).model_dump(mode="json"),
    )
    db.add(profile)

    raw_text_hash = hashlib.sha256(f"{user_id}{role_title}{company_name}".encode()).hexdigest()
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text=role_title,
        raw_text_hash=raw_text_hash,
        role_title=role_title,
        company_name=company_name,
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="mid",
        language_requirement="de",
    )
    db.add(job)

    flow = FlowSession(
        user_id=user_id,
        user_type="returning",
        current_step="cv_generation",
        available_actions={},
    )
    db.add(flow)
    await db.flush()

    app = Application(
        user_id=user_id,
        job_analysis_id=job.id,
        workflow_status=WorkflowStatus.completed,
        user_status=UserStatus.tracking,
        role_title=role_title,
        company_name=company_name,
        flow_session_id=flow.id,
    )
    db.add(app)

    cv = GeneratedCV(
        job_analysis_id=job.id,
        profile_id=profile.id,
        tailored_data={},
        template=template,
        status=status,
        expires_at=now + timedelta(days=90),
    )
    db.add(cv)
    await db.commit()
    return cv


@pytest.mark.asyncio
async def test_list_documents_returns_own_cvs(db):
    """list_documents returns CVs belonging to the requesting user."""
    from applire.services.documents import list_documents

    cv = await _seed_cv(db, _USER_A, "Senior Engineer", "Roche")
    result = await list_documents(user_id=_USER_A, db=db)

    assert result.total == 1
    assert result.items[0].cv_id == cv.id
    assert result.items[0].role_title == "Senior Engineer"
    assert result.items[0].company_name == "Roche"
    assert result.items[0].flow_id is not None


@pytest.mark.asyncio
async def test_list_documents_excludes_other_users(db):
    """list_documents never leaks CVs from another user."""
    from applire.services.documents import list_documents

    await _seed_cv(db, _USER_B, "QA Lead", "Bayer")
    result = await list_documents(user_id=_USER_A, db=db)

    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_list_documents_pagination(db):
    """page and page_size parameters work correctly."""
    from applire.services.documents import list_documents

    await _seed_cv(db, _USER_A, "Role A", "Company A")
    await _seed_cv(db, _USER_A, "Role B", "Company B")
    await _seed_cv(db, _USER_A, "Role C", "Company C")

    page1 = await list_documents(user_id=_USER_A, db=db, page=1, page_size=2)
    assert len(page1.items) == 2
    assert page1.total == 3

    page2 = await list_documents(user_id=_USER_A, db=db, page=2, page_size=2)
    assert len(page2.items) == 1


@pytest.mark.asyncio
async def test_list_documents_status_filter(db):
    """status query param filters by CVGenerationStatus."""
    from applire.services.documents import list_documents

    await _seed_cv(db, _USER_A, "Role A", "Co A", status="ready")
    await _seed_cv(db, _USER_A, "Role B", "Co B", status="generating")

    ready = await list_documents(user_id=_USER_A, db=db, status="ready")
    assert ready.total == 1
    assert ready.items[0].role_title == "Role A"
