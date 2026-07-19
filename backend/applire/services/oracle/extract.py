# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US243 — claim extraction for the Truthfulness Oracle (ADR-052 §2).

Structured documents (``tailored_data`` / ``letter_data``) are segmented into
claims fully deterministically — bullets are claims, prose fields are split
into sentences. The ONLY LLM touchpoint is the free-prose fallback for raw
external text whose blocks exceed :data:`ORACLE_PROSE_FALLBACK_CHARS` without
a single deterministic sentence boundary, and that call is bounded by
contract (ADR-047).
"""
from __future__ import annotations

import re
from typing import Any

from applire.constants import (
    ORACLE_MAX_SEGMENT_CALLS,
    ORACLE_PROSE_FALLBACK_CHARS,
    ORACLE_SEGMENT_MAX_TOKENS,
)
from applire.schemas.oracle import Claim

# Dotted abbreviations that must not terminate a sentence (DE + EN). Matching
# is case-sensitive on purpose: "No." the abbreviation is title-cased, while a
# sentence ending in "no." is a real boundary.
_ABBREVIATIONS = (
    "z.B.", "z. B.", "d.h.", "d. h.", "u.a.", "u. a.", "bzw.", "ggf.",
    "inkl.", "ca.", "vs.", "e.g.", "i.e.", "etc.", "approx.",
    "Dr.", "Prof.", "Nr.", "No.",
)
_SENTINEL = "\x00"

_BULLET_RE = re.compile(r"^[-•*–—]\s+(.*)$")
_DECIMAL_DOT_RE = re.compile(r"(?<=\d)\.(?=\d)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_MIN_CLAIM_CHARS = 3


def split_sentences(text: str) -> list[str]:
    """Deterministic sentence split with abbreviation and decimal guards."""
    t = (text or "").strip()
    if not t:
        return []
    protected = t
    for abbrev in _ABBREVIATIONS:
        protected = protected.replace(abbrev, abbrev.replace(".", _SENTINEL))
    protected = _DECIMAL_DOT_RE.sub(_SENTINEL, protected)
    sentences = []
    for part in _SENTENCE_SPLIT_RE.split(protected):
        restored = part.replace(_SENTINEL, ".").strip()
        if restored:
            sentences.append(restored)
    return sentences


def _sentence_claims(text: str, prefix: str) -> list[Claim]:
    return [
        Claim(text=s, location=f"{prefix}[{i}]", kind="sentence")
        for i, s in enumerate(split_sentences(text))
        if len(s) >= _MIN_CLAIM_CHARS
    ]


def _bullet_claims(bullets: Any, prefix: str, source_id: str | None = None) -> list[Claim]:
    claims: list[Claim] = []
    if not isinstance(bullets, list):
        return claims
    for i, b in enumerate(bullets):
        if isinstance(b, str) and len(b.strip()) >= _MIN_CLAIM_CHARS:
            claims.append(
                Claim(
                    text=b.strip(),
                    location=f"{prefix}[{i}]",
                    kind="bullet",
                    source_experience_id=source_id,
                )
            )
    return claims


def extract_claims_from_tailored(tailored_data: dict[str, Any]) -> list[Claim]:
    """Claims from a generated CV's ``tailored_data`` — deterministic, no LLM.

    Covered surfaces: summary sentences, work/project bullets (incl. projects
    nested under positions, US187), standalone project bullets, and the skills
    list (each skill is a checkable claim — the #192 skill-dump lesson).
    Certifications/education/languages are copied deterministically from the
    vault by the pipeline (PQ F7 / ADR-040) and are not re-audited in v1.
    """
    data = tailored_data or {}
    claims: list[Claim] = []
    claims += _sentence_claims(data.get("summary") or "", "summary")

    for wi, entry in enumerate(data.get("work_history") or []):
        if not isinstance(entry, dict):
            continue
        # TailoredWorkEntry.id = the source WorkEntry.id (US187) — the rendered
        # position, anchoring the v2 role-attribution check (#196). Empty for
        # legacy/mock data → None, and the matcher stays silent.
        source_id = entry.get("id") or None
        if not isinstance(source_id, str):
            source_id = None
        claims += _bullet_claims(entry.get("bullets"), f"work_history[{wi}].bullets", source_id)
        for pi, proj in enumerate(entry.get("projects") or []):
            if isinstance(proj, dict):
                claims += _bullet_claims(
                    proj.get("bullets"),
                    f"work_history[{wi}].projects[{pi}].bullets",
                    source_id,
                )

    for pi, proj in enumerate(data.get("projects") or []):
        if isinstance(proj, dict):
            claims += _bullet_claims(proj.get("bullets"), f"projects[{pi}].bullets")

    for si, skill in enumerate(data.get("skills") or []):
        if isinstance(skill, str) and len(skill.strip()) >= _MIN_CLAIM_CHARS:
            claims.append(Claim(text=skill.strip(), location=f"skills[{si}]", kind="skill"))

    return claims


def extract_claims_from_letter(letter_data: dict[str, Any]) -> list[Claim]:
    """Claims from a cover letter's ``letter_data`` body — deterministic."""
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    claims: list[Claim] = []
    for pi, para in enumerate(paragraphs or []):
        if isinstance(para, str):
            claims += _sentence_claims(para, f"body.paragraphs[{pi}]")
    return claims


_SEGMENT_PROMPT = (
    "Split the following resume/cover-letter prose into its individual factual "
    "claims (one short statement each). Return STRICT JSON: "
    '{{"claims": ["...", "..."]}}. Do not rephrase, do not add or drop content — '
    "segment only.\n\nTEXT:\n{text}"
)


async def _segment_prose_llm(text: str, provider: Any) -> list[str]:
    """ADR-047 bounded-output-by-contract prose segmentation fallback."""
    try:
        result = await provider.aparse_json(
            _SEGMENT_PROMPT.format(text=text),
            temperature=0.0,
            max_tokens=ORACLE_SEGMENT_MAX_TOKENS,
        )
    except Exception:
        return [text]
    claims = result.get("claims") if isinstance(result, dict) else None
    if not isinstance(claims, list):
        return [text]
    cleaned = [c.strip() for c in claims if isinstance(c, str) and c.strip()]
    return cleaned or [text]


async def extract_claims_from_text(text: str, provider: Any | None = None) -> list[Claim]:
    """Claims from raw external document text (US248 audit-any-document).

    Deterministic line/bullet/sentence segmentation; the LLM fallback fires
    ONLY for a block longer than ``ORACLE_PROSE_FALLBACK_CHARS`` in which the
    deterministic splitter found no sentence boundary at all — and at most
    ``ORACLE_MAX_SEGMENT_CALLS`` times per document (adversarial review
    2026-07-18 MAJOR-2: per-line fan-out on the agent-exposed tool). Once the
    budget is spent, a qualifying block degrades to a single claim.
    """
    claims: list[Claim] = []
    idx = 0
    segment_calls_left = ORACLE_MAX_SEGMENT_CALLS

    def _add(claim_text: str, kind: str) -> None:
        nonlocal idx
        if len(claim_text.strip()) >= _MIN_CLAIM_CHARS:
            claims.append(Claim(text=claim_text.strip(), location=f"text[{idx}]", kind=kind))
            idx += 1

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            _add(bullet.group(1), "bullet")
            continue
        sentences = split_sentences(line)
        if len(sentences) <= 1 and len(line) > ORACLE_PROSE_FALLBACK_CHARS:
            if provider is not None and segment_calls_left > 0:
                segment_calls_left -= 1
                segments = await _segment_prose_llm(line, provider)
            else:
                segments = [line]
            for s in segments:
                _add(s, "sentence")
        else:
            for s in sentences:
                _add(s, "sentence")

    return claims
