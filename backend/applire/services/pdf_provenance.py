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

"""ADR-085 — machine-readable AI-provenance marking of every rendered PDF.

Art. 50(2) of the EU AI Act (Regulation (EU) 2024/1689) requires the provider of
an AI system that generates synthetic text to mark its outputs in a
**machine-readable** format so they are detectable as artificially generated.
Applire integrates a third-party model over an API and is the provider of the
*system*, so the obligation is ours: the Commission Guidelines
``C(2026) 5054 final`` para. 27 only *encourage* model-level marking upstream,
and Art. 2(12)'s open-source exemption explicitly does not reach Art. 50.

**The seam.** Every PDF Applire produces is born at a Playwright
``page.pdf()`` call. This module owns that call — :func:`render_marked_pdf` —
so the mark is applied once, below the templates, below the document kind and
below both doors (REST and the ADR-054 agent channel). No other module may call
``page.pdf(``; ``tests/ats/test_pdf_provenance.py`` pins that population, so a
third renderer added later fails a named test instead of shipping unmarked.

**Two carriers, one claim.**

* The **XMP packet** is the primary, machine-readable claim: an *indirect*
  stream object referenced from the document catalog's ``/Metadata``, carrying
  IPTC's ``DigitalSourceType = trainedAlgorithmicMedia`` (the vocabulary C2PA
  and the large generative services share) alongside a documented Applire
  namespace.
* The **Info dictionary** keys are a convenience duplicate for a human who opens
  Document Properties. They have no standard and are not the claim.

**What the mark asserts:** the generation *event* — produced by Applire version
V at time T, text is ``trainedAlgorithmicMedia``. It asserts nothing about the
delivered content still being the generated content, and it carries no API
key, no exact model id, no configured provider name, and no user, job or
document identifier.

**What it does not survive.** The mark lives in a metadata layer. Measured
2026-09-04 with Ghostscript 10.07.0 ``-sDEVICE=pdfwrite``: the Applire XMP
namespace and every custom Info key are gone from the output. That is a property
of the layer, recorded in ``docs/ai-act-provenance.md`` and in
``tests/ats/test_pdf_provenance.py``'s round-trip recorder — not a defect here.

**The implementation trap, because the obvious route is wrong.** ``/Metadata``
must be an **indirect** object. pypdf's public ``PdfWriter.xmp_metadata`` setter
assigns the stream straight into the catalog dictionary; pypdf reads the result
back happily and **PyMuPDF cannot open it at all** ("invalid key in dict",
``page_count == 0``) — i.e. our own ATS text extractor would be locked out of
every document we ship. Hence ``_add_object`` below, and hence the presence test
asserts a *PyMuPDF* open. Verified identical on pypdf 5.1.0 and on the pinned
6.15.0 that CI and the image run.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

# --- the documented vocabulary ------------------------------------------------

#: The Applire AI-provenance namespace. Versioned in the URI: a breaking change
#: to the property set gets a new URI rather than a silent redefinition.
APPLIRE_NS_URI = "https://applire.de/ns/ai-provenance/1.0/"
APPLIRE_NS_PREFIX = "applireAI"

#: IPTC Extension — the interoperable half. `DigitalSourceType` is the property
#: C2PA and the large generative services use to say "a model made this".
IPTC_EXT_NS_URI = "http://iptc.org/std/Iptc4xmpExt/2008-02/"
IPTC_EXT_NS_PREFIX = "Iptc4xmpExt"
_IPTC_SOURCE_TYPE_BASE = "http://cv.iptc.org/newscodes/digitalsourcetype"
DIGITAL_SOURCE_TYPE = f"{_IPTC_SOURCE_TYPE_BASE}/trainedAlgorithmicMedia"

#: The IPTC term for a document mixing model output with other content.
#:
#: **Emitted since 2026-09-05 (founder ruling 14, v0.41.1-beta) for exactly one
#: class: a document authored through the BYOI agent door.** ``render_agent_cv``
#: / ``render_agent_letter`` (ADR-054 §4) persist content the CALLER's agent
#: wrote; Applire rendered it and cannot attest its authorship, so claiming
#: "a trained model made this" would be a claim about someone else's process.
#: Composite says what is true: model-made content passed through this renderer.
#:
#: A HAND-EDITED UI document (`SectionPatchRequest`) is deliberately NOT in this
#: class and stays uniform — that is ADR-067 territory and undecided. The
#: asymmetry is intentional: Art. 50(2)'s risk is asymmetric, an unmarked AI
#: output is the breach and an over-marked human edit is not.
COMPOSITE_DIGITAL_SOURCE_TYPE = (
    f"{_IPTC_SOURCE_TYPE_BASE}/compositeWithTrainedAlgorithmicMedia"
)

#: ``GeneratedCV.origin`` / ``GeneratedCoverLetter.origin`` (ADR-054, migration
#: 0051) for a document the caller's own agent authored through the BYOI door.
ORIGIN_AGENT = "agent"

XMP_NS_URI = "http://ns.adobe.com/xap/1.0/"

GENERATOR_NAME = "Applire"
MARKING_SPEC = "EU AI Act Art. 50(2)"

#: Info-dictionary keys — a convenience duplicate, never the primary claim.
#: Non-standard by construction (the PDF spec defines no AI-provenance key), so
#: they are namespaced by the ``AI`` prefix and documented here.
INFO_KEY_GENERATED = "/AIGenerated"
INFO_KEY_GENERATED_BY = "/AIGeneratedBy"
INFO_KEY_GENERATED_AT = "/AIGeneratedAt"
INFO_KEY_SOURCE_TYPE = "/AIDigitalSourceType"

#: Every Info key this module writes — the enumeration the tests read, so a key
#: added here without a test is impossible.
INFO_KEYS: tuple[str, ...] = (
    INFO_KEY_GENERATED,
    INFO_KEY_GENERATED_BY,
    INFO_KEY_GENERATED_AT,
    INFO_KEY_SOURCE_TYPE,
)

# pypdf does NOT validate Info-dictionary key names. ``add_metadata`` wraps the
# key in a ``NameObject`` without checking, and a key missing its leading "/"
# only produces a DeprecationWarning at write time — the malformed name is then
# written into the file, the Info dictionary becomes syntactically invalid, and
# a reader's recovery path silently DROPS entries. Measured on pypdf 5.1.0 by
# this ADR's adversarial pass: `add_metadata({"AIGenerated": "true"})` produced
# a PDF whose Info dict came back holding only `/Producer`, with no exception
# ever raised. That failure is invisible to a fail-closed contract, so the five
# literals above are checked here, at import, where a typo cannot reach a
# delivered document.
for _key in INFO_KEYS:
    if not re.fullmatch(r"/AI[A-Za-z]+", _key):  # noqa: PLR2004 - see comment
        raise RuntimeError(
            f"pdf_provenance: Info-dictionary key {_key!r} is malformed. pypdf "
            "would write it without complaint and silently corrupt the Info "
            "dictionary of every rendered PDF."
        )
del _key


@dataclass(frozen=True)
class Provenance:
    """The generation-event facts the mark carries. Deliberately small.

    Founder ruling (ADR-085 ruling 15, 2026-09-05): the mark does not name the
    configured LLM provider at all — an earlier field doing that here is gone,
    not merely left unset.
    """

    generator: str
    generator_version: str
    generated_at: str
    digital_source_type: str = DIGITAL_SOURCE_TYPE

    def as_info_dict(self) -> dict[str, str]:
        return {
            INFO_KEY_GENERATED: "true",
            INFO_KEY_GENERATED_BY: f"{self.generator} {self.generator_version}",
            INFO_KEY_GENERATED_AT: self.generated_at,
            INFO_KEY_SOURCE_TYPE: self.digital_source_type,
        }


def current_provenance(
    *,
    generated_at: datetime | None = None,
    digital_source_type: str = DIGITAL_SOURCE_TYPE,
) -> Provenance:
    """Build the provenance record for a render happening *now*."""
    from applire._version import __version__

    when = generated_at or datetime.now(timezone.utc)
    return Provenance(
        generator=GENERATOR_NAME,
        generator_version=__version__,
        generated_at=when.isoformat(),
        digital_source_type=digital_source_type,
    )


#: Characters XML 1.0 forbids outright (control characters other than tab, LF,
#: CR). ``escape`` handles ``& < >``; it does not handle these, and a stray one
#: — reaching a provenance value from mangled input, say — would make the
#: metadata stream unparseable for every reader while the PDF itself still
#: opened. Dropped rather than escaped: they carry no meaning here.
_XML_FORBIDDEN_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xml_text(value: object) -> str:
    """Escape *value* for an XML text node, and drop characters XML cannot hold.

    The packet is well-formed **by construction**: element names are the module
    constants above, and every value passes through here. The corresponding
    parse-it-back assertion lives in ``tests/ats/test_pdf_provenance.py`` — the
    place where parsing our own output costs nothing.
    """
    return escape(_XML_FORBIDDEN_RE.sub("", str(value)))


def build_xmp_packet(provenance: Provenance) -> bytes:
    """Serialise *provenance* as a standalone XMP packet (UTF-8 bytes).

    Chromium emits no ``/Metadata`` at all (measured 2026-09-04: the Info dict
    carries ``/Title /Creator /Producer /CreationDate /ModDate`` and nothing
    else), so this is always a fresh packet rather than a merge.
    """
    properties = (
        ("xmp:CreatorTool", f"{provenance.generator} {provenance.generator_version}"),
        (f"{IPTC_EXT_NS_PREFIX}:DigitalSourceType", provenance.digital_source_type),
        (f"{APPLIRE_NS_PREFIX}:aiGenerated", "true"),
        (f"{APPLIRE_NS_PREFIX}:generator", provenance.generator),
        (f"{APPLIRE_NS_PREFIX}:generatorVersion", provenance.generator_version),
        (f"{APPLIRE_NS_PREFIX}:generatedAt", provenance.generated_at),
        (f"{APPLIRE_NS_PREFIX}:markingSpec", MARKING_SPEC),
    )
    body = "\n".join(f"   <{k}>{_xml_text(v)}</{k}>" for k, v in properties)
    meta = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Applire">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        f'      xmlns:xmp="{XMP_NS_URI}"\n'
        f'      xmlns:{IPTC_EXT_NS_PREFIX}="{IPTC_EXT_NS_URI}"\n'
        f'      xmlns:{APPLIRE_NS_PREFIX}="{APPLIRE_NS_URI}">\n'
        f"{body}\n"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>"
    )
    packet = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        f"{meta}\n"
        '<?xpacket end="w"?>\n'
    )
    return packet.encode("utf-8")


def mark_pdf_bytes(pdf_bytes: bytes, provenance: Provenance | None = None) -> bytes:
    """Return *pdf_bytes* with the AI-provenance mark applied. Never visible.

    Raises rather than returning unmarked bytes (ADR-085 clause 7): this is a
    property of the artefact and a legal obligation, not an optional diagnostic
    about it, and a failure here means the input is not a PDF we should deliver
    either.
    """
    provenance = provenance or current_provenance()

    writer = PdfWriter(clone_from=io.BytesIO(pdf_bytes))

    stream = DecodedStreamObject()
    stream.set_data(build_xmp_packet(provenance))
    stream[NameObject("/Type")] = NameObject("/Metadata")
    stream[NameObject("/Subtype")] = NameObject("/XML")
    # MUST be indirect — see the module docstring. pypdf's public
    # ``xmp_metadata`` setter writes it inline and PyMuPDF then refuses the file.
    writer.root_object[NameObject("/Metadata")] = writer._add_object(stream)

    # A PDF is allowed to carry no /Info at all, and ``add_metadata`` asserts on
    # one (pypdf 5.1.0 ``_writer.py:1620``) — found by this ADR's own test on a
    # PyMuPDF-produced page, i.e. by a *valid* input, not a corrupt one. The
    # public ``metadata`` setter is not the fix: it CLEARS the dictionary first,
    # which would drop Chromium's /Title, and pypdf 6.15.0 dropped the branch
    # that creates the missing object anyway.
    if writer._info is None:
        writer._info = DictionaryObject()

    # Merges into Chromium's existing Info dict; /Title (the language-correct
    # document title of PR #647) and /Producer are preserved.
    writer.add_metadata(provenance.as_info_dict())

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def digital_source_type_for_origin(origin: str | None) -> str:
    """THE mapping from ADR-054's ``origin`` to ADR-085's ``DigitalSourceType``.

    One function, because the PDF seam and the ``.docx`` seam must never grow two
    answers to the same question (ADR-066), and because the join of those two
    ADRs is a decision — founder ruling 14 of 2026-09-05 — not an implementation
    detail either module should re-derive.

    Fail-SAFE, not fail-closed: an unknown, empty or NULL ``origin`` (a
    pre-migration row, a value some future writer invents) maps to the uniform
    ``trainedAlgorithmicMedia``. Only a door that KNOWS it did not author the
    content may claim composite; guessing composite for an unrecognised value
    would weaken the Art. 50(2) claim on documents Applire really did write.
    """
    return COMPOSITE_DIGITAL_SOURCE_TYPE if origin == ORIGIN_AGENT else DIGITAL_SOURCE_TYPE


async def render_marked_pdf(
    page: Any, *, provenance: "Provenance | None" = None, **pdf_options: Any
) -> bytes:
    """The single PDF render seam: Chromium's bytes, marked before anyone sees them.

    ``page`` is a Playwright ``Page``; ``pdf_options`` are passed through to
    ``page.pdf()`` unchanged, so each caller keeps its own format and margins.

    ``provenance`` (ADR-085 clause 1 stays intact: this is still the ONE seam, it
    just takes an argument) lets a caller holding a document row supply the mark
    whose ``digital_source_type`` it derived from that row's ``origin``. ``None``
    — every caller that does not know — keeps ``current_provenance()``, i.e.
    today's behaviour exactly.
    """
    return mark_pdf_bytes(await page.pdf(**pdf_options), provenance)


def read_provenance(pdf_bytes: bytes) -> dict[str, Any]:
    """Read the mark back out — the detection half of the same vocabulary.

    Returns ``{"xmp": {property: value}, "info": {key: value}}`` with the
    Applire/IPTC/xmp properties found in the XMP packet and the Info-dictionary
    duplicates. Both mappings are empty for an unmarked (or stripped) PDF, which
    is what makes this usable as a plain detector.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))

    xmp: dict[str, str] = {}
    raw = reader.xmp_metadata
    if raw is not None:
        text = raw.stream.get_data().decode("utf-8", "replace")
        for prefix in (APPLIRE_NS_PREFIX, IPTC_EXT_NS_PREFIX, "xmp"):
            for element in _iter_prefixed_elements(text, prefix):
                xmp[element[0]] = element[1]

    info: dict[str, str] = {}
    metadata = reader.metadata or {}
    for key in INFO_KEYS:
        if key in metadata:
            info[key] = str(metadata[key])

    return {"xmp": xmp, "info": info}


def is_marked(pdf_bytes: bytes) -> bool:
    """Does this PDF carry **Applire's** mark? The detector, and its own trap.

    Deliberately not ``bool(read_provenance(...)["xmp"])``: that returns any
    ``xmp:`` property, and a downstream normaliser writes its *own*
    ``xmp:CreateDate`` / ``xmp:CreatorTool`` while stripping ours. The first
    version of the Ghostscript round-trip test used exactly that predicate and
    reported "the mark survived" about a file whose Applire namespace and IPTC
    property were both gone — the instrument measured "is there any XMP".
    """
    found = read_provenance(pdf_bytes)
    if found["info"]:
        return True
    return any(
        key.startswith(f"{APPLIRE_NS_PREFIX}:")
        or key == f"{IPTC_EXT_NS_PREFIX}:DigitalSourceType"
        for key in found["xmp"]
    )


def _iter_prefixed_elements(xmp_text: str, prefix: str):
    """Yield ``(qualified_name, text)`` for ``<prefix:name>value</prefix:name>``.

    A deliberately small reader over the packet's own serialisation rather than
    a namespace-aware XML walk: the packet is produced by
    :func:`build_xmp_packet` two functions up, so its shape is ours, and a
    stripped or foreign packet correctly yields nothing.
    """
    pattern = re.compile(
        rf"<({re.escape(prefix)}:[A-Za-z0-9_.-]+)>(.*?)</\1>", re.DOTALL
    )
    for match in pattern.finditer(xmp_text):
        yield match.group(1), match.group(2).strip()
