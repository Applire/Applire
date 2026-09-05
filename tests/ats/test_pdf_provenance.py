# Copyright (C) 2026 Tobias Rosenbaum
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

"""ADR-085 / #577 — the AI-provenance mark, measured on real rendered PDFs.

Blocking, same status as ``test_roundtrip.py`` (ADR-039): every shipped template
is rendered through the real seam and the mark is read back out of the bytes a
user would receive. The vocabulary's own properties — packet shape, the
Info-key guard, the detector's negative controls, and the "no other module calls
``page.pdf()``" tripwire — live in ``tests/unit/test_pdf_provenance.py``, which
runs in the hermetic tree that does not skip itself without Chromium.

Four things measured here, each because assuming it would have been wrong:

1. **Presence** on all 7 CV templates x {de,en} and all 7 letter templates x
   {de,en}.
2. **The second seam.** ``cover_letter_pdf.render_pdf`` is the production letter
   renderer; the ADR-039 round-trip suite renders letter templates through
   ``cv.py::_html_to_pdf`` and never touches it. It gets its own seam test, so
   reverting the hook in that module alone reddens exactly one named test.
3. **PyMuPDF must be able to open the marked file.** pypdf's public
   ``xmp_metadata`` setter writes the XMP stream *inline* into the catalog;
   pypdf reads that back happily while PyMuPDF — our own ATS text extractor —
   reports "invalid key in dict" and zero pages. A pypdf-only assertion would
   have passed while every delivered document became unreadable to the audit.
4. **Invisibility**, as extracted-text and page-count equality between the
   unmarked Chromium bytes and the marked ones.

Plus the Ghostscript round-trip **recorder** — a pin on the measured fact the
public note quotes, not a gate on a promise. See its own docstring.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from applire.services import cover_letter_pdf
from applire.services.ats_audit import extract_text
from applire.services.color_detection import _default_context
from applire.services.cover_letter import _TEMPLATE_FILES as LETTER_TEMPLATES
from applire.services.cover_letter import _default_color_context
from applire.services.cv import _TEMPLATE_FILES as CV_TEMPLATES
from applire.services.cv import _html_to_pdf, _jinja_env
from applire.services.pdf_provenance import (
    APPLIRE_NS_PREFIX,
    DIGITAL_SOURCE_TYPE,
    INFO_KEYS,
    is_marked,
    mark_pdf_bytes,
    read_provenance,
)
from applire.templates.labels import cover_letter_labels, cv_labels

from test_roundtrip import CV_DE, CV_EN, LETTER_DE, LETTER_EN

_CV_FIXTURES = {"de": CV_DE, "en": CV_EN}
_LETTER_FIXTURES = {"de": LETTER_DE, "en": LETTER_EN}
_LANGS = ["de", "en"]

_MINIMAL_LETTER_HTML = (
    "<html><head><title>Anschreiben</title></head>"
    "<body><p>Sehr geehrte Damen und Herren,</p><p>Bewerbung.</p></body></html>"
)


def _assert_marked(
    pdf: bytes, label: str, source_type: str = DIGITAL_SOURCE_TYPE
) -> dict:
    """The one predicate every presence test asserts, so it cannot drift.

    ``source_type`` defaults to the uniform ``trainedAlgorithmicMedia``; ADR-085
    ruling 14 (2026-09-05) gives the BYOI agent door the composite value, and
    that caller states it here rather than growing a second predicate.
    """
    import fitz  # PyMuPDF — property 3 in the module docstring

    document = fitz.open(stream=pdf, filetype="pdf")
    assert document.page_count >= 1, (
        f"{label}: PyMuPDF cannot read the marked PDF (page_count="
        f"{document.page_count}). This is the inline-/Metadata failure ADR-085 "
        "clause 8 describes — the stream must be an INDIRECT object."
    )

    found = read_provenance(pdf)
    xmp, info = found["xmp"], found["info"]

    assert xmp.get(f"{APPLIRE_NS_PREFIX}:aiGenerated") == "true", f"{label}: XMP {xmp}"
    assert xmp.get("Iptc4xmpExt:DigitalSourceType") == source_type, (
        f"{label}: the interoperable half is missing or wrong — {xmp}"
    )
    for key in INFO_KEYS:
        assert key in info, f"{label}: Info key {key} missing — {info}"
    assert info["/AIGenerated"] == "true", f"{label}: {info}"
    return found


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", _LANGS)
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_cv_template_pdf_carries_the_provenance_mark(template, lang):
    html = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=_CV_FIXTURES[lang],
        color=_default_context(),
        lang=lang,
        labels=cv_labels(lang),
    )
    _assert_marked(await _html_to_pdf(html), f"cv/{template}/{lang}")


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", _LANGS)
@pytest.mark.parametrize("template", sorted(LETTER_TEMPLATES))
async def test_letter_template_pdf_carries_the_provenance_mark(template, lang):
    html = _jinja_env.get_template(LETTER_TEMPLATES[template]).render(
        letter=_LETTER_FIXTURES[lang],
        color=_default_color_context(),
        lang=lang,
        labels=cover_letter_labels(lang),
    )
    _assert_marked(await _html_to_pdf(html), f"letter/{template}/{lang}")


def _letter_render_pdf(monkeypatch, origin: str | None):
    """Drive ``cover_letter_pdf.render_pdf``'s real body with only its session
    and HTML source stubbed — Chromium, the mark and the read-back are real.
    ``origin`` is what the stub session reports for the row (ADR-054)."""

    class _NullSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def scalar(self, *args, **kwargs):
            return origin

    async def _html(cl_id, db, require_ready=True):
        return _MINIMAL_LETTER_HTML

    monkeypatch.setattr(cover_letter_pdf, "AsyncSessionLocal", lambda: _NullSession())
    monkeypatch.setattr(cover_letter_pdf, "get_cover_letter_html", _html)
    return cover_letter_pdf.render_pdf(uuid4())


@pytest.mark.asyncio
async def test_cover_letter_render_seam_marks_its_own_output(monkeypatch):
    """The SECOND Chromium call site, driven through its real function body."""
    _assert_marked(await _letter_render_pdf(monkeypatch, "pipeline"), "cover_letter_pdf")


@pytest.mark.asyncio
async def test_cv_render_seam_marks_an_agent_row_composite():
    """ADR-085 ruling 14 on the CV Chromium seam, real render, real read-back.
    ``get_cv_pdf`` derives this argument from the row's ``origin`` (ADR-054);
    here the seam itself is shown to carry the value through to the file."""
    from applire.services.pdf_provenance import COMPOSITE_DIGITAL_SOURCE_TYPE

    html = _jinja_env.get_template(CV_TEMPLATES["classic_german"]).render(
        cv=_CV_FIXTURES["de"],
        color=_default_context(),
        lang="de",
        labels=cv_labels("de"),
    )
    _assert_marked(
        await _html_to_pdf(html, digital_source_type=COMPOSITE_DIGITAL_SOURCE_TYPE),
        "cv/_html_to_pdf/agent",
        COMPOSITE_DIGITAL_SOURCE_TYPE,
    )


@pytest.mark.asyncio
async def test_cover_letter_render_seam_marks_an_agent_row_composite(monkeypatch):
    """ADR-085 ruling 14 at the DELIVERY point, through real Chromium: a letter
    row the BYOI door authored (``origin='agent'``, ADR-054 §4) carries
    ``compositeWithTrainedAlgorithmicMedia`` — Applire rendered that content and
    cannot attest its authorship. The test above is its negative control."""
    from applire.services.pdf_provenance import COMPOSITE_DIGITAL_SOURCE_TYPE

    _assert_marked(
        await _letter_render_pdf(monkeypatch, "agent"),
        "cover_letter_pdf/agent",
        COMPOSITE_DIGITAL_SOURCE_TYPE,
    )


@pytest.mark.asyncio
async def test_marking_changes_no_text_and_no_page_count():
    """ADR-085 clause 4 — "no visible change", measured rather than asserted.

    Renders once through raw Chromium — deliberately bypassing the seam, which
    is why this file is the one place that may — and compares the extraction of
    the unmarked bytes against the marked ones.
    """
    from playwright.async_api import async_playwright

    html = _jinja_env.get_template(CV_TEMPLATES["classic_german"]).render(
        cv=CV_DE, color=_default_context(), lang="de", labels=cv_labels("de")
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        raw = await page.pdf(  # the unmarked control
            format="A4",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        await browser.close()

    assert not is_marked(raw), (
        "the unmarked control already reports a mark — this test could then not "
        "tell marked from unmarked"
    )

    marked = mark_pdf_bytes(raw)
    assert extract_text(marked) == extract_text(raw)

    import fitz

    assert (
        fitz.open(stream=marked, filetype="pdf").page_count
        == fitz.open(stream=raw, filetype="pdf").page_count
    )


_MARK_SURVIVES_A_GHOSTSCRIPT_REWRITE = False
"""What we MEASURED, not what we promise (ADR-085 clause 6).

Ghostscript 10.07.0 ``-sDEVICE=pdfwrite``, 2026-09-04: the Applire XMP namespace
is gone, the IPTC property is gone and every custom Info key is gone — the output
carries Ghostscript's own ``xmp:CreateDate``/``xmp:CreatorTool`` instead, which
is exactly why ``is_marked`` and not "is there any XMP" is the predicate below.

This constant is a **pin on that observation**, because
``docs/ai-act-provenance.md`` quotes it as a fact. If this test goes red the
observation changed: re-measure, update the constant AND the public note. It is
not a gate on a promise — we promise only that the mark is generation-time and
may not survive reprocessing, which holds either way.
"""


@pytest.mark.asyncio
async def test_ghostscript_roundtrip_records_whether_the_mark_survives():
    ghostscript = shutil.which("gs")
    if ghostscript is None:
        pytest.skip(
            "Ghostscript is not installed here — ADR-085 clause 6's "
            "downstream-normaliser round-trip cannot be measured in this "
            "environment (qpdf is not installed either)."
        )

    html = _jinja_env.get_template(CV_TEMPLATES["classic_german"]).render(
        cv=CV_DE, color=_default_context(), lang="de", labels=cv_labels("de")
    )
    marked = await _html_to_pdf(html)
    _assert_marked(marked, "gs/input")

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "marked.pdf"
        target = Path(tmp) / "normalised.pdf"
        source.write_bytes(marked)
        result = subprocess.run(
            [
                ghostscript,
                "-q",
                "-dNOPAUSE",
                "-dBATCH",
                "-sDEVICE=pdfwrite",
                f"-sOutputFile={target}",
                str(source),
            ],
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr.decode()[:400]
        normalised = target.read_bytes()
        after = read_provenance(normalised)

    # The text still survives — it is only the metadata layer that does not.
    assert extract_text(normalised).strip(), "the normaliser destroyed the document"

    survived = is_marked(normalised)
    assert survived is _MARK_SURVIVES_A_GHOSTSCRIPT_REWRITE, (
        "The measured behaviour of a downstream PDF normaliser changed: the mark "
        f"now {'survives' if survived else 'does not survive'} Ghostscript "
        "-sDEVICE=pdfwrite, while _MARK_SURVIVES_A_GHOSTSCRIPT_REWRITE says "
        f"{_MARK_SURVIVES_A_GHOSTSCRIPT_REWRITE}. Update this constant AND the "
        f"public note in docs/ai-act-provenance.md. Observed: {after}"
    )
