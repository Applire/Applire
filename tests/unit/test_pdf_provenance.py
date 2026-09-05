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

"""ADR-085 — the AI-provenance vocabulary and its writer, without Chromium.

The real-render matrix lives in ``tests/ats/test_pdf_provenance.py``. These are
the properties that must hold in the **hermetic** tree, because
``tests/ats/`` skips itself wholesale when Chromium is unavailable and a mark
that is only proven by a skippable suite is not proven.

Two of these tests exist because the property they assert was violated by code
that looked correct and passed everything else:

* ``test_marking_a_pdf_without_an_info_dictionary``: a PDF may legitimately
  carry no ``/Info``, and pypdf's ``add_metadata`` *asserts* on that. Found by a
  PyMuPDF-produced page — a valid input, not a corrupt one.
* ``test_info_dictionary_keys_are_validated_at_import``: pypdf does not check
  key names. A key missing its leading ``/`` is written anyway (a
  DeprecationWarning, no exception), the Info dictionary becomes invalid, and a
  reader silently drops entries — a corruption a fail-closed contract cannot
  see, because nothing ever raises.
"""
import ast
import io
from pathlib import Path
from xml.etree import ElementTree

import fitz
import pytest

from applire.services.pdf_provenance import (
    APPLIRE_NS_PREFIX,
    APPLIRE_NS_URI,
    COMPOSITE_DIGITAL_SOURCE_TYPE,
    DIGITAL_SOURCE_TYPE,
    INFO_KEYS,
    IPTC_EXT_NS_URI,
    MARKING_SPEC,
    Provenance,
    build_xmp_packet,
    current_provenance,
    is_marked,
    mark_pdf_bytes,
    read_provenance,
)

_PROVENANCE = Provenance(
    generator="Applire",
    generator_version="0.0.0-test",
    generated_at="2026-09-04T12:00:00+00:00",
    model_provider="mistral",
)


def _blank_pdf(pages: int = 1) -> bytes:
    document = fitz.open()
    for _ in range(pages):
        document.new_page()
    return document.tobytes()


def test_xmp_packet_is_well_formed_and_declares_both_namespaces():
    packet = build_xmp_packet(_PROVENANCE).decode("utf-8")
    inner = packet[
        packet.index("<x:xmpmeta") : packet.index("</x:xmpmeta>") + len("</x:xmpmeta>")
    ]
    ElementTree.fromstring(inner)  # our own output; a parse failure IS the finding
    assert APPLIRE_NS_URI in packet
    assert IPTC_EXT_NS_URI in packet
    assert MARKING_SPEC in packet
    assert packet.startswith("<?xpacket begin=")
    assert packet.rstrip().endswith("<?xpacket end=\"w\"?>")


def test_xmp_values_that_would_break_the_packet_are_neutralised():
    """A value carrying markup or a control character must not produce broken XML."""
    hostile = Provenance(
        generator="Applire",
        generator_version="</applireAI:generatorVersion><evil/>",
        generated_at="2026-09-04T12:00:00+00:00",
        model_provider="mis\x00tral & co <script>",
    )
    packet = build_xmp_packet(hostile).decode("utf-8")
    inner = packet[
        packet.index("<x:xmpmeta") : packet.index("</x:xmpmeta>") + len("</x:xmpmeta>")
    ]
    ElementTree.fromstring(inner)
    assert "<evil/>" not in packet
    assert "\x00" not in packet


def test_marked_pdf_carries_both_carriers_and_reads_back():
    marked = mark_pdf_bytes(_blank_pdf(), _PROVENANCE)
    found = read_provenance(marked)

    assert found["xmp"][f"{APPLIRE_NS_PREFIX}:aiGenerated"] == "true"
    assert found["xmp"][f"{APPLIRE_NS_PREFIX}:generatorVersion"] == "0.0.0-test"
    assert found["xmp"][f"{APPLIRE_NS_PREFIX}:modelProvider"] == "mistral"
    assert found["xmp"]["Iptc4xmpExt:DigitalSourceType"] == DIGITAL_SOURCE_TYPE
    assert set(found["info"]) == set(INFO_KEYS)
    assert is_marked(marked)


def test_the_mark_never_carries_a_key_or_a_model_id():
    """ADR-085 clause 3 — what the mark must NOT say."""
    provenance = current_provenance(model_provider="openrouter")
    marked = mark_pdf_bytes(_blank_pdf(), provenance)
    blob = marked.decode("latin-1")
    assert "openrouter" in blob
    for forbidden in ("sk-", "api_key", "Bearer ", "openrouter/"):
        assert forbidden not in blob, forbidden


def test_marking_a_pdf_without_an_info_dictionary():
    """pypdf's ``add_metadata`` asserts on a missing ``/Info``; a valid PDF may have none."""
    source = _blank_pdf()
    assert b"/Info" not in source, "fixture no longer exercises the missing-/Info case"
    assert is_marked(mark_pdf_bytes(source, _PROVENANCE))


def test_marking_preserves_an_existing_info_dictionary():
    document = fitz.open()
    document.new_page()
    document.set_metadata({"title": "Lebenslauf – Anna Bauer", "producer": "Skia/PDF"})
    marked = mark_pdf_bytes(document.tobytes(), _PROVENANCE)

    from pypdf import PdfReader

    metadata = PdfReader(io.BytesIO(marked)).metadata
    assert metadata["/Title"] == "Lebenslauf – Anna Bauer"
    assert metadata["/AIGenerated"] == "true"


def test_marking_leaves_the_page_tree_alone():
    source = _blank_pdf(pages=3)
    marked = mark_pdf_bytes(source, _PROVENANCE)
    assert fitz.open(stream=marked, filetype="pdf").page_count == 3


def test_is_marked_says_no_for_an_unmarked_pdf():
    unmarked = _blank_pdf()
    assert not is_marked(unmarked)
    assert read_provenance(unmarked) == {"xmp": {}, "info": {}}


def test_is_marked_ignores_a_foreign_xmp_packet():
    """The detector's own trap — see ``is_marked``'s docstring.

    A downstream normaliser strips our packet and writes its own. A predicate of
    the form "is there any XMP" then reports the mark as surviving, which is what
    the first version of the Ghostscript round-trip test did.
    """
    document = fitz.open()
    document.new_page()
    document.set_xml_metadata(
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
        "<xmp:CreatorTool>Some Other Tool</xmp:CreatorTool>"
        "</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end=\"w\"?>"
    )
    foreign = document.tobytes()
    assert read_provenance(foreign)["xmp"], "fixture carries no XMP — test is vacuous"
    assert not is_marked(foreign)


def test_info_dictionary_keys_are_validated_at_import():
    """The guard that covers what a fail-closed contract cannot see.

    pypdf writes a key missing its leading ``/`` without raising and the Info
    dictionary silently loses entries, so the five literals are checked where a
    typo cannot reach a delivered document.
    """
    import re

    for key in INFO_KEYS:
        assert re.fullmatch(r"/AI[A-Za-z]+", key), key


def test_the_composite_source_type_is_available_but_unused():
    """ADR-085 clause 3's open decision, pinned so it stays visible.

    The seam cannot tell a fully generated document from one carrying a human
    section override or agent-supplied verbatim content, so everything is marked
    ``trainedAlgorithmicMedia``. The IPTC term for the mixed case exists in the
    module; the day a caller threads that fact through, this test is what says
    the vocabulary was already there.
    """
    assert COMPOSITE_DIGITAL_SOURCE_TYPE.endswith("compositeWithTrainedAlgorithmicMedia")
    assert current_provenance().digital_source_type == DIGITAL_SOURCE_TYPE
    custom = current_provenance(digital_source_type=COMPOSITE_DIGITAL_SOURCE_TYPE)
    marked = mark_pdf_bytes(_blank_pdf(), custom)
    assert read_provenance(marked)["xmp"]["Iptc4xmpExt:DigitalSourceType"] == (
        COMPOSITE_DIGITAL_SOURCE_TYPE
    )


def test_pdf_provenance_is_the_only_module_that_calls_page_pdf():
    """Population tripwire (ADR-085 clause 1) — NOT the enforcement.

    A third renderer added later would ship unmarked PDFs and every existing
    test would stay green, because none of them knows it exists. Enumerating the
    positive set is the only way to see that. The behavioural proof that the two
    known seams actually mark lives in ``tests/ats/test_pdf_provenance.py``.

    An AST walk rather than a grep: this file and both seams *mention*
    ``page.pdf(`` in prose, and the first version of this test failed on its own
    documentation.
    """
    backend = Path(__file__).resolve().parents[2] / "backend" / "applire"
    offenders: list[str] = []
    for path in sorted(backend.rglob("*.py")):
        if path.name == "pdf_provenance.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "pdf"
            ):
                offenders.append(f"{path.relative_to(backend)}:{node.lineno}")

    assert offenders == [], (
        "These call Chromium's page.pdf() directly and therefore ship UNMARKED "
        f"PDFs (ADR-085 clause 1): {offenders}. Route them through "
        "applire.services.pdf_provenance.render_marked_pdf."
    )


@pytest.mark.parametrize("pages", [1, 2, 5])
def test_marking_cost_is_bounded(pages):
    """ADR-085's recorded cost — a growth blow-up would be a delivery regression."""
    source = _blank_pdf(pages)
    marked = mark_pdf_bytes(source, _PROVENANCE)
    assert len(marked) < len(source) + 8192
