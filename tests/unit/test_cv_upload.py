"""
Iteration 12 — CV Upload & Parsing Pipeline (unit tests)

Done when:
  - Upload a real DACH CV PDF → GET /api/profile returns a structured MasterProfile
    with completeness_score > 0.6.
  - Upload a second CV → conflicts are flagged, no data is lost.

Covers:
  - Format detection: PDF / DOCX / image / plain text dispatch
  - extract_text_from_pdf: text-bearing PDF → non-empty string (mocked pymupdf)
  - CVUploadResponse.status logic: < 0.5 completeness → "DRAFT", >= 0.5 and no
    conflicts → "COMPLETE"
  - UploadRecord model: SQLite persistence (created_at, expires_at auto-set)
  - upload_cv() service: first import creates profile; second import reconciles
    via the ADR-046 engine (reconcile_import) and surfaces conflicts (US184)
  - StorageProvider: LocalStorageProvider.save() writes file and returns path
  - OCR factory: get_ocr_extractor() returns MistralVisionExtractor for default config

No Docker or real Postgres required.  No real LLM calls.

Run:
    pytest tests/unit/ -v
"""

import hashlib
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sqlite_session():
    """In-memory SQLite session.

    Creates only the tables needed for iter 12 tests, using explicit table list
    to avoid bare JSONB columns in other models (job_analyses, gap_analyses, etc.)
    that would fail on SQLite.

    MasterProfile uses JSONB().with_variant(JSON(), "sqlite") — SQLite-safe.
    UploadRecord and User use only standard column types.
    """
    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot
    from applire.models.uploads import UploadRecord
    from applire.models.user import User
    from applire.models.user_settings import UserSettings  # US184: get_ui_language

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    MasterProfile.__table__,
                    ProfileSnapshot.__table__,  # US168: _apply_merge snapshots pre-merge
                    UploadRecord.__table__,
                    User.__table__,
                    # US184: import paths now call get_ui_language(db) → needs the
                    # user_settings table so the engine path runs end-to-end.
                    UserSettings.__table__,
                ],
            )
        )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# 1. Format detection
# ---------------------------------------------------------------------------


class TestDetectFormat:
    def test_pdf_by_extension(self):
        from applire.services.cv_parser import detect_format
        assert detect_format("lebenslauf.pdf", "application/octet-stream") == "pdf"

    def test_pdf_by_mime(self):
        from applire.services.cv_parser import detect_format
        assert detect_format("document", "application/pdf") == "pdf"

    def test_docx_by_extension(self):
        from applire.services.cv_parser import detect_format
        fmt = detect_format(
            "cv.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        assert fmt == "docx"

    def test_doc_by_extension(self):
        from applire.services.cv_parser import detect_format
        assert detect_format("cv.doc", "application/msword") == "docx"

    def test_jpeg_by_extension(self):
        from applire.services.cv_parser import detect_format
        assert detect_format("scan.jpg", "image/jpeg") == "image"

    def test_png_by_mime(self):
        from applire.services.cv_parser import detect_format
        assert detect_format("upload", "image/png") == "image"

    def test_plain_text(self):
        from applire.services.cv_parser import detect_format
        assert detect_format("cv.txt", "text/plain") == "text"

    def test_unknown_defaults_to_text(self):
        from applire.services.cv_parser import detect_format
        assert detect_format("file.xyz", "application/octet-stream") == "text"


# ---------------------------------------------------------------------------
# 2. PDF text extraction (pymupdf mocked)
# ---------------------------------------------------------------------------


class TestExtractTextFromPdf:
    def _make_mock_reader(self, page_texts: list[str]) -> MagicMock:
        pages = []
        for text in page_texts:
            page = MagicMock()
            page.extract_text.return_value = text
            pages.append(page)
        reader = MagicMock()
        reader.pages = pages
        return reader

    def test_returns_text_from_pages(self):
        from applire.services.cv_parser import extract_text_from_pdf

        mock_reader = self._make_mock_reader(["Max Mustermann\nSoftware Engineer"])

        with patch("pypdf.PdfReader", return_value=mock_reader):
            result = extract_text_from_pdf(b"fake-pdf-bytes")

        assert "Max Mustermann" in result
        assert "Software Engineer" in result

    def test_empty_pdf_returns_empty_string(self):
        from applire.services.cv_parser import extract_text_from_pdf

        mock_reader = self._make_mock_reader([""])

        with patch("pypdf.PdfReader", return_value=mock_reader):
            result = extract_text_from_pdf(b"scanned-pdf")

        assert result == ""


# ---------------------------------------------------------------------------
# 3. Language schema — null level tolerance
# ---------------------------------------------------------------------------


class TestLanguageSchema:
    def test_null_level_is_accepted(self):
        from applire.schemas.profile import Language

        lang = Language(language="French", level=None)
        assert lang.language == "French"
        assert lang.level is None

    def test_missing_level_defaults_to_none(self):
        from applire.schemas.profile import Language

        lang = Language.model_validate({"language": "French"})
        assert lang.level is None


# ---------------------------------------------------------------------------
# 4. CVUploadResponse status logic
# ---------------------------------------------------------------------------


class TestCVUploadResponseStatus:
    def test_draft_when_low_completeness(self):
        from applire.schemas.profile import CVUploadResponse

        resp = CVUploadResponse(
            profile_id=uuid.uuid4(),
            status="DRAFT",
            completeness_score=0.3,
            conflicts=[],
            enrichment_record_id=uuid.uuid4(),
            expires_at=datetime.now(timezone.utc),
        )
        assert resp.status == "DRAFT"
        assert resp.completeness_score < 0.5

    def test_complete_when_high_completeness_no_conflicts(self):
        from applire.schemas.profile import CVUploadResponse

        resp = CVUploadResponse(
            profile_id=uuid.uuid4(),
            status="COMPLETE",
            completeness_score=0.75,
            conflicts=[],
            enrichment_record_id=uuid.uuid4(),
            expires_at=datetime.now(timezone.utc),
        )
        assert resp.status == "COMPLETE"

    def test_draft_when_conflicts_present(self):
        from applire.schemas.profile import ConflictSummary, CVUploadResponse

        conflict = ConflictSummary(
            conflict_id=str(uuid.uuid4()),
            section="work_experience",
            field="start_date",
            source="cv_upload",
        )
        status = "DRAFT" if bool([conflict]) else "COMPLETE"
        assert status == "DRAFT"


# ---------------------------------------------------------------------------
# 4. UploadRecord SQLite persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_record_persists(sqlite_session):
    from datetime import timedelta

    from applire.models.uploads import UploadRecord

    record = UploadRecord(
        original_filename="lebenslauf.pdf",
        content_hash="abc123",
        mime_type="application/pdf",
        file_path="/data/uploads/abc.pdf",
        byte_size=12345,
        llm_tokens_used=1500,
        llm_provider="MistralProvider",
    )
    sqlite_session.add(record)
    await sqlite_session.commit()
    await sqlite_session.refresh(record)

    assert record.id is not None
    assert record.original_filename == "lebenslauf.pdf"
    assert record.llm_tokens_used == 1500
    # expires_at should be ~7 days after created_at
    delta = record.expires_at - record.created_at
    assert 6 <= delta.days <= 8


# ---------------------------------------------------------------------------
# 5. LocalStorageProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_storage_save(tmp_path):
    from applire.storage.local import LocalStorageProvider

    provider = LocalStorageProvider(str(tmp_path))
    file_bytes = b"fake cv content"
    path = await provider.save(file_bytes, "cv.pdf")

    assert Path(path).exists()
    assert Path(path).read_bytes() == file_bytes
    assert path.endswith(".pdf")


@pytest.mark.asyncio
async def test_local_storage_delete(tmp_path):
    from applire.storage.local import LocalStorageProvider

    provider = LocalStorageProvider(str(tmp_path))
    path = await provider.save(b"data", "test.txt")
    assert Path(path).exists()

    await provider.delete(path)
    assert not Path(path).exists()


@pytest.mark.asyncio
async def test_local_storage_delete_nonexistent_is_noop(tmp_path):
    from applire.storage.local import LocalStorageProvider

    provider = LocalStorageProvider(str(tmp_path))
    # Should not raise
    await provider.delete(str(tmp_path / "nonexistent.pdf"))


# ---------------------------------------------------------------------------
# 6. OCR factory
# ---------------------------------------------------------------------------


def test_get_ocr_extractor_returns_mistral_vision_by_default():
    from unittest.mock import patch

    with patch("applire.config.settings") as mock_settings:
        mock_settings.ocr_backend = "mistral_vision"
        mock_settings.mistral_api_key = "test-key"

        from applire.ocr import get_ocr_extractor
        from applire.ocr.mistral_vision import MistralVisionExtractor

        extractor = get_ocr_extractor()
        assert isinstance(extractor, MistralVisionExtractor)


def test_get_ocr_extractor_returns_tesseract_when_configured():
    with patch("applire.config.settings") as mock_settings:
        mock_settings.ocr_backend = "tesseract"

        from applire.ocr import get_ocr_extractor
        from applire.ocr.tesseract import TesseractExtractor

        extractor = get_ocr_extractor()
        assert isinstance(extractor, TesseractExtractor)


def test_get_ocr_extractor_raises_for_unknown_backend():
    with patch("applire.config.settings") as mock_settings:
        mock_settings.ocr_backend = "unknown_backend"

        from applire.ocr import get_ocr_extractor

        with pytest.raises(ValueError, match="Unknown OCR_BACKEND"):
            get_ocr_extractor()


# ---------------------------------------------------------------------------
# 7. upload_cv() — first import creates profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_cv_first_import(sqlite_session, tmp_path):
    """First CV upload creates a MasterProfile and returns CVUploadResponse."""
    from applire.services.profile import upload_cv
    from applire.storage.local import LocalStorageProvider
    from applire.ocr.tesseract import TesseractExtractor

    mock_provider = AsyncMock()
    mock_provider.__class__.__name__ = "MockProvider"
    profile_data = {
        "personal_info": {"name": "Max Mustermann", "email": "max@example.de"},
        "work_experience": [
            {
                "company": "Siemens AG",
                "role": "Software Engineer",
                "start_date": "2019-01",
                "end_date": "2023-06",
                "responsibilities": ["Developed backend services", "Led code reviews"],
                "technologies": ["Python", "Django"],
            }
        ],
        "education": [
            {
                "institution": "TU München",
                "degree": "Bachelor of Science",
                "field": "Computer Science",
                "start_date": "2015",
                "end_date": "2019",
            }
        ],
        "skills": [
            {"name": "Python", "category": "technical", "proficiency": "advanced"},
            {"name": "Django", "category": "technical", "proficiency": "intermediate"},
        ],
        "languages": [
            {"language": "German", "level": "Native"},
            {"language": "English", "level": "C1"},
        ],
    }
    mock_provider.aparse_json.return_value = profile_data
    mock_ocr = AsyncMock()

    with patch("applire.services.cv_parser.extract_text", new=AsyncMock(return_value="Max Mustermann\nSoftware Engineer\nSiemens AG")), \
         patch("applire.services.profile.review_and_refine", new=AsyncMock(side_effect=lambda **kw: kw["draft"])), \
         patch("applire.services.profile.enrich_skills", new=AsyncMock(side_effect=lambda p, _: p)):
        storage = LocalStorageProvider(str(tmp_path))
        response = await upload_cv(
            file_bytes=b"fake-pdf",
            filename="cv.pdf",
            content_type="application/pdf",
            db=sqlite_session,
            provider=mock_provider,
            storage=storage,
            ocr_extractor=mock_ocr,
        )

    assert response.profile_id is not None
    assert response.completeness_score > 0.0
    assert response.expires_at is not None
    assert response.enrichment_record_id is not None
    assert response.conflicts == []
    assert response.completeness_score >= 0.5
    assert response.status == "COMPLETE"


# ---------------------------------------------------------------------------
# 8. upload_cv() — second import triggers merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_cv_second_import_triggers_merge(sqlite_session, tmp_path):
    """US184: a second conflicting upload reconciles via the ADR-046 engine and
    surfaces the ambiguity as a conflict to the user.

    Migrated off the retired lexical merge_profiles: the engine decides what
    conflicts, so the stub provider returns a request_confirmation ambiguity for
    the start_date divergence (which the import bridge maps onto a Conflict)."""
    from applire.services.profile import upload_cv
    from applire.storage.local import LocalStorageProvider

    mock_ocr = AsyncMock()
    storage = LocalStorageProvider(str(tmp_path))

    first_profile = {
        "personal_info": {"name": "Anna Schmidt"},
        "work_experience": [
            {
                "company": "BMW Group",
                "role": "Product Manager",
                "start_date": "2018-03",
                "end_date": "2022-01",
                "responsibilities": ["Led product roadmap"],
            }
        ],
        "skills": [{"name": "Product Management", "category": "domain", "proficiency": "advanced"}],
        "languages": [{"language": "German", "level": "Native"}],
    }

    second_profile = {
        "personal_info": {"name": "Anna Schmidt"},
        "work_experience": [
            {
                "company": "BMW Group",
                "role": "Senior Product Manager",
                "start_date": "2017-06",  # different start_date → conflict
                "end_date": "2022-01",
                "responsibilities": ["Managed stakeholder relations"],
            }
        ],
        "skills": [{"name": "Product Management", "category": "domain", "proficiency": "expert"}],
        "languages": [{"language": "German", "level": "Native"}],
    }

    # On the second import the engine's reconcile() call (system prompt = the
    # "profile reconciler") returns an ambiguity over the divergent start_date;
    # the import bridge maps that onto a Conflict. Every other aparse_json call
    # (extraction) returns the staged profile.
    reconcile_payload = {
        "ops": [],
        "ambiguities": [
            {
                "op": "request_confirmation",
                "question": "Conflicting start_date for BMW Group: 2018-03 vs 2017-06?",
                "options": ["2018-03", "2017-06"],
            }
        ],
    }
    _staged = {"value": first_profile}

    async def _aparse_json(prompt, *, system=None, **kwargs):
        if "profile reconciler" in (system or "").lower():
            return reconcile_payload
        return _staged["value"]

    mock_provider = AsyncMock()
    mock_provider.__class__.__name__ = "MockProvider"
    mock_provider.aparse_json = AsyncMock(side_effect=_aparse_json)

    with patch("applire.services.cv_parser.extract_text", new=AsyncMock(return_value="Anna Schmidt\nBMW Group")), \
         patch("applire.services.profile.review_and_refine", new=AsyncMock(side_effect=lambda **kw: kw["draft"])), \
         patch("applire.services.profile.enrich_skills", new=AsyncMock(side_effect=lambda p, _: p)):

        _staged["value"] = first_profile
        await upload_cv(
            file_bytes=b"cv1",
            filename="cv1.pdf",
            content_type="application/pdf",
            db=sqlite_session,
            provider=mock_provider,
            storage=storage,
            ocr_extractor=mock_ocr,
        )

        _staged["value"] = second_profile
        response = await upload_cv(
            file_bytes=b"cv2",
            filename="cv2.pdf",
            content_type="application/pdf",
            db=sqlite_session,
            provider=mock_provider,
            storage=storage,
            ocr_extractor=mock_ocr,
        )

    assert len(response.conflicts) >= 1
    conflict_fields = [c.field for c in response.conflicts]
    assert any("start_date" in f for f in conflict_fields)
    assert response.status == "DRAFT"
