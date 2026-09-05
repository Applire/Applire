# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-085, founder ruling 14 (2026-09-05) — the agent door marks COMPOSITE.

A document whose CONTENT came verbatim from an external agent through the BYOI
door (ADR-054 §4: ``render_agent_cv`` / ``render_agent_letter``) is marked
``compositeWithTrainedAlgorithmicMedia``: Applire only rendered it and cannot
attest its authorship. Everything Applire's own writer produced stays
``trainedAlgorithmicMedia``.

The mark is decided from the persisted row's ``origin`` column (ADR-054,
migration 0051), NOT from which function happened to render the bytes — because
the delivered bytes are produced by ``GET /api/cv/{id}/pdf`` and
``GET /api/cv/{id}/docx`` at any later time, from the row, long after the agent
call returned.
"""
import io
import uuid

import fitz
import pytest

from applire.services.office_export.provenance import (
    PROP_SOURCE_TYPE,
    read_document_provenance,
)
from applire.services.pdf_provenance import (
    COMPOSITE_DIGITAL_SOURCE_TYPE,
    DIGITAL_SOURCE_TYPE,
    IPTC_EXT_NS_PREFIX,
    INFO_KEY_SOURCE_TYPE,
    current_provenance,
    digital_source_type_for_origin,
    mark_pdf_bytes,
    read_provenance,
)

_XMP_SOURCE_TYPE = f"{IPTC_EXT_NS_PREFIX}:DigitalSourceType"


def _blank_pdf() -> bytes:
    document = fitz.open()
    document.new_page()
    return document.tobytes()


def _marked_source_type(origin: str) -> str:
    prov = current_provenance(digital_source_type=digital_source_type_for_origin(origin))
    return read_provenance(mark_pdf_bytes(_blank_pdf(), prov))["xmp"][_XMP_SOURCE_TYPE]


# --------------------------------------------------------------------------
# The mapping itself — ADR-054 `origin` -> ADR-085 DigitalSourceType
# --------------------------------------------------------------------------
class TestDigitalSourceTypeForOrigin:
    def test_agent_origin_maps_to_composite(self):
        assert digital_source_type_for_origin("agent") == COMPOSITE_DIGITAL_SOURCE_TYPE

    def test_pipeline_origin_maps_to_the_uniform_default(self):
        assert digital_source_type_for_origin("pipeline") == DIGITAL_SOURCE_TYPE

    def test_unknown_and_missing_origins_stay_uniform(self):
        """Fail-SAFE, not fail-closed, and deliberately so: an unrecognised or
        NULL origin is a pipeline document until something says otherwise. Only
        the door that knows it did not author the content claims composite."""
        for value in (None, "", "ui", "Agent", "AGENT"):
            assert digital_source_type_for_origin(value) == DIGITAL_SOURCE_TYPE


# --------------------------------------------------------------------------
# The PDF half — both values survive a real mark/read round trip
# --------------------------------------------------------------------------
class TestPdfMarkCarriesBothSourceTypes:
    @pytest.mark.parametrize(
        "origin,expected",
        [("pipeline", DIGITAL_SOURCE_TYPE), ("agent", COMPOSITE_DIGITAL_SOURCE_TYPE)],
    )
    def test_read_provenance_returns_the_source_type_it_was_marked_with(
        self, origin, expected
    ):
        prov = current_provenance(digital_source_type=digital_source_type_for_origin(origin))
        found = read_provenance(mark_pdf_bytes(_blank_pdf(), prov))
        assert found["xmp"][_XMP_SOURCE_TYPE] == expected
        assert found["info"][INFO_KEY_SOURCE_TYPE] == expected

    def test_the_two_values_are_actually_different(self):
        """Guards the parametrised test above against a mapping that collapsed
        both origins onto one constant — then both cases would pass vacuously."""
        pipeline = _marked_source_type("pipeline")
        agent = _marked_source_type("agent")
        assert pipeline != agent
        assert agent.endswith("compositeWithTrainedAlgorithmicMedia")
        assert pipeline.endswith("trainedAlgorithmicMedia")


# --------------------------------------------------------------------------
# The .docx half — the export reads the ROW, so it can mark composite too
# --------------------------------------------------------------------------
class TestDocxMarkCarriesBothSourceTypes:
    @pytest.mark.parametrize(
        "origin,expected",
        [("pipeline", DIGITAL_SOURCE_TYPE), ("agent", COMPOSITE_DIGITAL_SOURCE_TYPE)],
    )
    def test_new_document_stamps_the_requested_source_type(self, origin, expected):
        from applire.services.office_export._common import new_document

        doc = new_document(
            title="T", digital_source_type=digital_source_type_for_origin(origin)
        )
        buf = io.BytesIO()
        doc.save(buf)
        assert read_document_provenance(buf.getvalue())[PROP_SOURCE_TYPE] == expected

    def test_omitting_the_argument_keeps_the_uniform_default(self):
        """Every writer that does not know who authored the content — and every
        pre-existing caller — is unchanged (ADR-085 clause 1: one seam)."""
        from applire.services.office_export._common import new_document

        buf = io.BytesIO()
        new_document(title="T").save(buf)
        assert (
            read_document_provenance(buf.getvalue())[PROP_SOURCE_TYPE]
            == DIGITAL_SOURCE_TYPE
        )


# --------------------------------------------------------------------------
# The seams: the delivery functions must READ the row, not a render-time flag
# --------------------------------------------------------------------------
class TestDeliverySeamsReadTheRowsOrigin:
    """The BYOI door persists the row and returns ``/api/cv/{id}/pdf`` and
    ``/api/cv/{id}/docx`` (mcp/server.py ``render_document``) — the delivered
    bytes are produced by those endpoints, on that request and on every later
    download. So the mark has to come off the row, and these tests drive the
    real service functions with the render step stubbed out.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "origin,expected",
        [("pipeline", DIGITAL_SOURCE_TYPE), ("agent", COMPOSITE_DIGITAL_SOURCE_TYPE)],
    )
    async def test_get_cv_pdf_passes_the_rows_origin_to_the_render_seam(
        self, monkeypatch, origin, expected
    ):
        from applire.services import cv as cv_svc

        seen: dict = {}

        async def _fake_html(cv_id, db):
            return "<html></html>"

        async def _fake_pdf(html, *, digital_source_type=None):
            seen["dst"] = digital_source_type
            return b"%PDF-1.4"

        class _Row:
            def __init__(self, origin):
                self.origin = origin

        async def _fake_load(cv_id, db):
            return _Row(origin)

        monkeypatch.setattr(cv_svc, "get_cv_html", _fake_html)
        monkeypatch.setattr(cv_svc, "_html_to_pdf", _fake_pdf)
        monkeypatch.setattr(cv_svc, "_load_cv_ready", _fake_load)

        await cv_svc.get_cv_pdf(uuid.uuid4(), db=None)
        assert seen["dst"] == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "origin,expected",
        [("pipeline", DIGITAL_SOURCE_TYPE), ("agent", COMPOSITE_DIGITAL_SOURCE_TYPE)],
    )
    async def test_get_cv_docx_passes_the_rows_origin_to_the_writer(
        self, monkeypatch, origin, expected
    ):
        from applire.services import cv as cv_svc
        from applire.services.office_export import cv_docx as cv_docx_mod

        seen: dict = {}

        class _Row:
            def __init__(self, origin):
                self.origin = origin

        async def _fake_load(cv_id, db):
            return _Row(origin)

        async def _fake_prep(record, db):
            return (object(), "de", "#123456", None)

        def _fake_render(tailored, *, lang, accent_color, photo_bytes=None,
                         digital_source_type=None):
            seen["dst"] = digital_source_type
            return b"docx"

        monkeypatch.setattr(cv_svc, "_load_cv_ready", _fake_load)
        monkeypatch.setattr(cv_svc, "_prepare_cv_docx_render", _fake_prep)
        monkeypatch.setattr(cv_docx_mod, "render_cv_docx", _fake_render)

        await cv_svc.get_cv_docx(uuid.uuid4(), db=None)
        assert seen["dst"] == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "origin,expected",
        [("pipeline", DIGITAL_SOURCE_TYPE), ("agent", COMPOSITE_DIGITAL_SOURCE_TYPE)],
    )
    async def test_get_cover_letter_docx_passes_the_rows_origin_to_the_writer(
        self, monkeypatch, origin, expected
    ):
        from applire.models.cover_letter import CoverLetterStatus
        from applire.services import cover_letter as cl_svc
        from applire.services.office_export import letter_docx as letter_docx_mod

        seen: dict = {}

        class _Row:
            id = uuid.uuid4()
            status = CoverLetterStatus.ready.value

            def __init__(self, origin):
                self.origin = origin

        class _Result:
            def scalar_one_or_none(self_inner):
                return _Row(origin)

        class _DB:
            async def execute(self_inner, *a, **kw):
                return _Result()

        async def _fake_prep(cl, db):
            return (object(), "de", "#123456")

        def _fake_render(letter, *, lang, accent_color, digital_source_type=None):
            seen["dst"] = digital_source_type
            return b"docx"

        monkeypatch.setattr(cl_svc, "_prepare_cover_letter_docx_render", _fake_prep)
        monkeypatch.setattr(letter_docx_mod, "render_letter_docx", _fake_render)

        await cl_svc.get_cover_letter_docx(uuid.uuid4(), _DB())
        assert seen["dst"] == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "origin,expected",
        [("pipeline", DIGITAL_SOURCE_TYPE), ("agent", COMPOSITE_DIGITAL_SOURCE_TYPE)],
    )
    async def test_letter_render_pdf_passes_the_rows_origin_to_the_render_seam(
        self, monkeypatch, origin, expected
    ):
        """The fourth call site. ``cover_letter_pdf.render_pdf`` opens its own
        session, so it reads ``origin`` itself — the agent door's pre-audit
        render and every later download of the same row then agree."""
        import contextlib

        from applire.services import cover_letter_pdf as pdf_mod

        seen: dict = {}

        class _DB:
            async def scalar(self_inner, *a, **kw):
                return origin

        @contextlib.asynccontextmanager
        async def _session():
            yield _DB()

        async def _fake_html(cl_id, db, require_ready=True):
            return "<html></html>"

        class _Page:
            async def set_content(self_inner, *a, **kw):
                return None

        class _Browser:
            async def new_page(self_inner):
                return _Page()

            async def close(self_inner):
                return None

        class _Chromium:
            async def launch(self_inner):
                return _Browser()

        class _PW:
            chromium = _Chromium()

        @contextlib.asynccontextmanager
        async def _playwright():
            yield _PW()

        async def _fake_marked(page, *, provenance=None, **opts):
            seen["dst"] = provenance.digital_source_type if provenance else None
            return b"%PDF-1.4"

        monkeypatch.setattr(pdf_mod, "AsyncSessionLocal", _session)
        monkeypatch.setattr(pdf_mod, "get_cover_letter_html", _fake_html)
        monkeypatch.setattr(pdf_mod, "async_playwright", _playwright)
        monkeypatch.setattr(pdf_mod, "render_marked_pdf", _fake_marked)

        await pdf_mod.render_pdf(uuid.uuid4())
        assert seen["dst"] == expected
