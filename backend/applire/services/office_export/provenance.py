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

"""ADR-085 clause 5 — the `.docx` export's half of the AI-provenance mark.

The exported `.docx` is a **second delivered artefact** (ADR-079 clause 2), not
a second rendering of the PDF, so Art. 50(2) reaches it on its own account. The
claim is identical to the PDF's and is built from the same
:class:`applire.services.pdf_provenance.Provenance` record — one vocabulary, two
carriers (ADR-066: one implementation per capability).

**The carrier is different because the format is.** OOXML has no XMP surface,
and `python-docx` 1.1.2 exposes none. What OOXML *does* have is
`docProps/custom.xml` — **custom document properties**, which Word and
LibreOffice both surface (File → Info → Properties → Advanced) and preserve.
That is the machine-readable provenance surface of the format, so that is where
the mark goes; `docProps/core.xml` carries the human-readable duplicate, exactly
as the PDF's Info dictionary does.

`python-docx` has no API for custom properties (no `customprops.py`, no
`CustomProperties` anywhere in 1.1.2), so the part is built from `docx.opc`
primitives: a plain :class:`~docx.opc.part.Part` with the custom-properties
content type, related from the **package** (not the document part), which is
what puts it in `_rels/.rels` and gets it an `[Content_Types].xml` override.
``OpcPackage.iter_parts`` walks from ``package.rels``, so ``document.save()``
needs no change at all — no subclassing, no monkeypatching, no private
serialisation override.

**Verified, not assumed** (2026-09-04): the produced package re-opens in
`python-docx`, converts cleanly through headless LibreOffice
(`soffice --headless --convert-to pdf`, which `tests/ats/test_office_export.py`
already runs as a hard CI gate), and leaves
`office_export.extract.extract_docx_text` — the ADR-039 audit's input — byte-for-byte
unchanged.

**Known gap, recorded rather than hidden:** no Microsoft Word was available to
test against. LibreOffice does not validate `pid` at all (it accepted duplicate
and zero-based pids in a four-variant probe), so the ECMA-376 rule — `pid`
starts at 2 and increments — is honoured here as a *producer obligation*, not
because any consumer was observed enforcing it.
"""
from __future__ import annotations

from docx.opc.constants import CONTENT_TYPE as CT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from lxml import etree

from applire.services.pdf_provenance import (
    MARKING_SPEC,
    Provenance,
    current_provenance,
)

CUSTOM_PROPS_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
)
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

#: The fixed format id every OOXML custom property carries (ECMA-376).
CUSTOM_PROPS_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"

CUSTOM_PROPS_PARTNAME = "/docProps/custom.xml"

#: Custom-property names. Deliberately the Info-dictionary key names of
#: ``pdf_provenance`` minus the leading "/", so a reader who has learned one
#: carrier can read the other.
PROP_GENERATED = "AIGenerated"
PROP_GENERATED_BY = "AIGeneratedBy"
PROP_GENERATED_AT = "AIGeneratedAt"
PROP_SOURCE_TYPE = "AIDigitalSourceType"
PROP_MARKING_SPEC = "AIMarkingSpec"

CUSTOM_PROP_NAMES: tuple[str, ...] = (
    PROP_GENERATED,
    PROP_GENERATED_BY,
    PROP_GENERATED_AT,
    PROP_SOURCE_TYPE,
    PROP_MARKING_SPEC,
)


def provenance_properties(provenance: Provenance) -> dict[str, str]:
    """The mark as an ordered ``{name: value}`` mapping (the same claim as the XMP)."""
    return {
        PROP_GENERATED: "true",
        PROP_GENERATED_BY: f"{provenance.generator} {provenance.generator_version}",
        PROP_GENERATED_AT: provenance.generated_at,
        PROP_SOURCE_TYPE: provenance.digital_source_type,
        PROP_MARKING_SPEC: MARKING_SPEC,
    }


def build_custom_properties_xml(properties: dict[str, str]) -> bytes:
    root = etree.Element(
        f"{{{CUSTOM_PROPS_NS}}}Properties", nsmap={"op": CUSTOM_PROPS_NS, "vt": VT_NS}
    )
    # ECMA-376: pid is 1-based with 0 and 1 reserved, so the first property is 2.
    for pid, (name, value) in enumerate(properties.items(), start=2):
        element = etree.SubElement(root, f"{{{CUSTOM_PROPS_NS}}}property")
        element.set("fmtid", CUSTOM_PROPS_FMTID)
        element.set("pid", str(pid))
        element.set("name", name)
        etree.SubElement(element, f"{{{VT_NS}}}lpwstr").text = value
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def mark_document(document, *, title: str | None = None, provenance: Provenance | None = None) -> None:
    """Apply the AI-provenance mark to a freshly created ``python-docx`` document.

    ``title`` closes writer-collector #601's line: `office_export` set **no**
    core properties at all, so a delivered `.docx` carried an empty Title while
    the PDF carried a language-correct one (PR #647). The two delivery formats
    now identify themselves the same way.
    """
    provenance = provenance or current_provenance()
    properties = provenance_properties(provenance)

    core = document.core_properties
    if title:
        core.title = title
    core.comments = (
        f"AI-generated with {properties[PROP_GENERATED_BY]} "
        f"on {properties[PROP_GENERATED_AT]}. Marked under {MARKING_SPEC}."
    )
    core.keywords = "AI-generated"

    package = document.part.package
    part = Part(
        PackURI(CUSTOM_PROPS_PARTNAME),
        CT.OFC_CUSTOM_PROPERTIES,
        build_custom_properties_xml(properties),
        package,
    )
    package.relate_to(part, RT.CUSTOM_PROPERTIES)


def read_document_provenance(docx_bytes: bytes) -> dict[str, str]:
    """Read the custom-property mark back out of `.docx` bytes.

    The detection half, and the negative control for the tests: an unmarked file
    has no ``docProps/custom.xml`` and yields ``{}``.
    """
    import zipfile
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(docx_bytes)) as archive:
        if CUSTOM_PROPS_PARTNAME.lstrip("/") not in archive.namelist():
            return {}
        blob = archive.read(CUSTOM_PROPS_PARTNAME.lstrip("/"))

    # The part is ours, but this helper is also the documented way to verify a
    # file that came back from somewhere else — so no entity resolution and no
    # network, rather than trusting provenance to prove provenance.
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.fromstring(blob, parser=parser)
    found: dict[str, str] = {}
    for element in root.findall(f"{{{CUSTOM_PROPS_NS}}}property"):
        value = element.find(f"{{{VT_NS}}}lpwstr")
        found[element.get("name", "")] = (value.text or "") if value is not None else ""
    return found
