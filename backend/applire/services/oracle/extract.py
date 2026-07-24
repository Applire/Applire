# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US243 — claim extraction for the Truthfulness Oracle (ADR-052 §2).

Structured documents (``tailored_data`` / ``letter_data``) are segmented into
claims fully deterministically — bullets are claims, prose fields are split
into sentences. Letter sentences are further split into clause-level claims
(:func:`split_clauses`, #237) and, where the sentence names exactly one known
employer/project, stamped with that role's id (:func:`extract_claims_from_letter`)
— the same ``source_experience_id`` anchor the CV path stamps from
``TailoredWorkEntry.id`` (US187), making the v2 attribution matcher (#196)
reachable for letters. The ONLY LLM touchpoint is the free-prose fallback for
raw external text whose blocks exceed :data:`ORACLE_PROSE_FALLBACK_CHARS`
without a single deterministic sentence boundary, and that call is bounded by
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
from applire.services.ats_audit import skill_tokens

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

# ── #237 — clause-level decomposition + employer anchoring for letters ──────
# Real-model letter prose uses typographic punctuation; fold it to ASCII
# before anchor matching (the U+2019 lesson, 2026-07-11 — same pattern as
# oracle/stance.py's ``_APOSTROPHES``).
_APOSTROPHE_CHARS = "’ʼ‘‛´`"
_DASH_CHARS = "‒–—―−"


def _normalize_punct(text: str) -> str:
    out = text
    for ch in _APOSTROPHE_CHARS:
        out = out.replace(ch, "'")
    for ch in _DASH_CHARS:
        out = out.replace(ch, "-")
    return out


# ── courtesy/meta formula filter (adversarial-pass residual, 2026-07-23) ────
# An entirely honest letter still scored unverifiable-dominated because pure
# courtesy openers/closers ("I am writing to express my interest…", "Thank
# you for your time and consideration.") were extracted as claims and, having
# no vault-checkable content, piled into the unverifiable bucket. These are
# formulas, not factual claims about the candidate, so they must never be
# extracted at all — but conservatively: a clause that ALSO carries a factual
# assertion keeps its full original text as a real claim (see
# ``_is_pure_formula_clause`` below).
#
# Each pattern's own bounded tail (where present) consumes the typical
# short "for/in the {role} at {company}" framing that belongs to the SAME
# formula, not a separate fact — real multi-fact sentences bolt a second
# fact on via a clause-boundary conjunction (``split_clauses`` above already
# isolates that fact into its own clause before this filter ever runs). The
# tail is length-bounded (not ``.*``) so it cannot silently swallow an
# unrelated later fact within the same unsplit clause.
_ROLE_TAIL = r"(?:\s+(?:for|in)\s+.{0,90})?"

_FORMULA_SEED_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        # EN openers
        rf"i am writing to (?:you )?(?:express my (?:strong |keen |sincere )?interest|apply){_ROLE_TAIL}",
        rf"i am (?:excited|thrilled|pleased|delighted|honou?red) to apply{_ROLE_TAIL}",
        r"with (?:great|keen|genuine|much) (?:interest|enthusiasm)",
        r"it is with (?:great|much) pleasure",
        # EN closers / availability
        r"thank you for (?:your|the) (?:time|consideration|attention)",
        r"i appreciate (?:your|the) (?:time|consideration)",
        r"please do not hesitate to contact me",
        r"i (?:remain|am) available (?:for an interview|at your convenience|to discuss)",
        r"at your earliest convenience",
        r"i would welcome the opportunity to discuss",
        r"i look forward to (?:discussing|the opportunity to discuss|your response|scheduling|speaking with you)",
        # DE openers/closers
        r"sehr geehrte[rn]?",
        r"mit freundlichen gr[uü]ßen",
        r"mit (?:gro[ßs]em|gro[ßs]er|viel) interesse",
        r"vielen dank f[uü]r ihre (?:zeit|aufmerksamkeit|ber[uü]cksichtigung)",
        r"ich freue mich (?:auf ein (?:pers[oö]nliches )?gespr[aä]ch|[uü]ber die m[oö]glichkeit)",
        r"stehe ich (?:ihnen )?gerne (?:f[uü]r ein gespr[aä]ch )?zur verf[uü]gung",
    )
]

# Residual filler that survives seed removal but still carries no candidate
# fact (the reader-facing framing of the SAME formula, not the applicant's
# own evidence) — only ever subtracted from a clause a seed already matched.
_FORMULA_FRAMING_WORDS = frozenset(
    {
        "role", "position", "opportunity", "team", "company", "organisation",
        "organization", "firm", "employer", "consideration", "convenience",
        "interview", "application", "vacancy", "opening", "advertised",
        "regarding", "concerning", "this", "that", "further", "it",
        "write", "writing", "you", "i", "my",
        # DE
        "stelle", "unternehmen", "gelegenheit", "bewerbung", "vorstellungsgespraech",
        "vorstellungsgespräch", "gespraech", "gespräch", "beruecksichtigung",
        "berücksichtigung", "zeit", "aufmerksamkeit", "interesse",
        "damen", "herren", "und", "sowie",
    }
)


def _is_pure_formula_clause(text: str) -> bool:
    """True when ``text`` is a courtesy/meta formula with no substantive claim.

    Conservative by construction: only clauses that match at least one known
    formula seed are considered at all, and even then only when NO content
    tokens survive after stripping the matched seed(s) and the reader-facing
    framing words (reusing ``skill_tokens`` — the shared tokenizer, never a
    fork). A clause naming no formula seed, or one that keeps a real fact
    after the formula is removed, is left untouched and extracted as usual.
    """
    normalized = _normalize_punct(text)
    residual = normalized
    matched = False
    for pattern in _FORMULA_SEED_PATTERNS:
        new_residual, n = pattern.subn(" ", residual)
        if n:
            matched = True
            residual = new_residual
    if not matched:
        return False
    return not (skill_tokens(residual) - _FORMULA_FRAMING_WORDS)


# Clause boundaries: a semicolon, a comma followed by a coordinating
# conjunction/preposition that typically introduces a bolted-on second fact
# (EN+DE), or a spaced em/en-dash. The delimiter itself is consumed — it
# never survives into either resulting clause. Deliberately NOT a bare
# " and "/" und " (no comma) — that would shred ordinary noun phrases
# ("Python and Java") the letter path never sees as bullets.
_CLAUSE_BOUNDARY_RE = re.compile(
    r";\s+"
    r"|,\s+(?:with|including|and|und|einschließlich|inklusive|mit)\s+"
    rf"|\s[{_DASH_CHARS}-]\s",
    re.IGNORECASE,
)


def split_clauses(text: str) -> list[str]:
    """Deterministic clause-level split for narrative sentences (#237).

    A multi-fact sentence — "At BioNTech, I led AI automation projects …
    with comprehensive testing, observability, and reliability practices."
    — almost never clears ``GROUNDED_MIN_COVERAGE`` as a single claim: its
    content tokens span an employer, an activity, and several unrelated
    practice areas, so no single vault evidence unit can cover 60% of them.
    Splitting on clause boundaries turns it into several smaller,
    independently checkable claims. Falls back to the whole sentence when no
    boundary is found — the common case stays a single ``sentence`` claim.
    """
    t = (text or "").strip()
    if not t:
        return []
    parts = [p.strip() for p in _CLAUSE_BOUNDARY_RE.split(t)]
    return [p for p in parts if p]


def _profile_get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _employer_anchor_candidates(profile: Any | None) -> list[tuple[str, str]]:
    """(surface name, experience id) pairs a letter sentence can anchor to.

    Covers work-experience company names and project names (own id, or the
    parent work id when ``associated_experience`` resolves — mirroring the
    US187 nesting rule vault.py already applies). Duck-types over a
    ``MasterProfileData`` or its raw JSONB dict — extraction runs before the
    vault index exists, so it never requires a full model coercion.
    """
    if profile is None:
        return []
    pairs: list[tuple[str, str]] = []
    work_ids: set[str] = set()
    for w in _profile_get(profile, "work_experience") or []:
        wid = _profile_get(w, "id")
        company = _profile_get(w, "company")
        if isinstance(wid, str) and wid.strip() and isinstance(company, str) and company.strip():
            wid = wid.strip()
            work_ids.add(wid)
            pairs.append((company.strip(), wid))

    for pr in _profile_get(profile, "projects") or []:
        name = _profile_get(pr, "name")
        pid = _profile_get(pr, "id")
        assoc = _profile_get(pr, "associated_experience")
        target = pid.strip() if isinstance(pid, str) and pid.strip() else None
        if isinstance(assoc, str) and assoc.strip() and assoc.strip() in work_ids:
            target = assoc.strip()
        if isinstance(name, str) and name.strip() and target:
            pairs.append((name.strip(), target))

    return pairs


def _find_employer_anchor(sentence: str, candidates: list[tuple[str, str]]) -> str | None:
    """The experience id a sentence anchors to, or ``None`` (fail open).

    A sentence naming EXACTLY one known employer/project stamps every claim
    derived from it — "At BioNTech," / "bei BioNTech" (DE) alike, since
    matching is plain normalized substring containment, prefix-agnostic. A
    sentence naming two or more distinct known employers stays unanchored
    rather than risk mis-anchoring (adversarial-review lesson: fail open,
    never fail wrong).
    """
    if not candidates:
        return None
    normalized_sentence = _normalize_punct(sentence)
    found: set[str] = set()
    for name, entity_id in candidates:
        pattern = re.compile(
            r"\b" + re.escape(_normalize_punct(name)) + r"\b", re.IGNORECASE
        )
        if pattern.search(normalized_sentence):
            found.add(entity_id)
    if len(found) == 1:
        return next(iter(found))
    return None


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


def _bullet_claims(
    bullets: Any, prefix: str, source_id: str | None = None
) -> list[Claim]:
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
        claims += _bullet_claims(
            entry.get("bullets"), f"work_history[{wi}].bullets", source_id
        )
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


def extract_claims_from_letter(
    letter_data: dict[str, Any], profile: Any | None = None
) -> list[Claim]:
    """Claims from a cover letter's ``letter_data`` body — deterministic.

    #237 (F14): a whole narrative sentence almost never clears the grounding
    coverage floor, and letters structurally could not stamp
    ``source_experience_id`` at all — so a misattributed blend (real
    BioNTech achievement + unrelated interview evidence, blended into one
    sentence) filed as merely "unverifiable". Two additive steps fix this
    without touching the frozen writer:

    1. Each sentence is decomposed into clause-level claims
       (:func:`split_clauses`) — smaller, independently checkable fragments.
       A sentence with no clause boundary stays a single ``sentence`` claim
       at the same location as before (backward compatible).
    2. When the sentence names EXACTLY one employer/project from ``profile``
       (:func:`_find_employer_anchor`), every claim derived from it is
       stamped with that role's id — making the v2 attribution matcher
       (#196) reachable for letters for the first time.
    """
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    candidates = _employer_anchor_candidates(profile)
    claims: list[Claim] = []
    for pi, para in enumerate(paragraphs or []):
        if not isinstance(para, str):
            continue
        for si, sentence in enumerate(split_sentences(para)):
            if len(sentence) < _MIN_CLAIM_CHARS:
                continue
            anchor = _find_employer_anchor(sentence, candidates)
            clauses = split_clauses(sentence)
            base = f"body.paragraphs[{pi}][{si}]"
            multi = len(clauses) > 1
            for ci, clause in enumerate(clauses):
                if len(clause) < _MIN_CLAIM_CHARS:
                    continue
                if _is_pure_formula_clause(clause):
                    continue
                claims.append(
                    Claim(
                        text=clause,
                        location=f"{base}.clauses[{ci}]" if multi else base,
                        kind="clause" if multi else "sentence",
                        source_experience_id=anchor,
                    )
                )
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
