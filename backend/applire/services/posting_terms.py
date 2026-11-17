# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q10 — posting terms: a met requirement in the posting's word is the
person's only once they say so (rulings Q7-2, Q10-1…Q10-6; ADR-048 amended
2026-11-17).

Delivery 2b: nine items on two personas came from ledger rows the classifier
graded ``direct`` ("Observability", "Security engineering", "Industrie-
controlling" …). The person had described the work in their own words and never
used the posting's label, and the writer printed the label as a skill chip. The
``posting_word_only`` stamp (WP-Q7) reads only ``partial`` rows.

This module is the FACT the rest reads (ADR-062 cl. 1, no judgement):

* :func:`posting_term_rows` — the claimable ``direct`` rows whose CONCEPT is not
  the person's word. The concept is theirs when:
  - (a) it occurs as a whole token in the coverage corpus (skill aliases included);
  - (b) a spelling of it does: the closed acronym table, or an acronym the person
    defined in their own text;
  - (c) it equals a word the person wrote with a German suspended hyphen, expanded;
  - (d) it is itself written with a suspended hyphen and every word is held;
  - (e) it is a coordination whose every part is held;
  - (f) it is a claimed phrase inside one of their denial statements (MD-Q18
    clause rule);
  - (g) it is a near-duplicate of a held skill name or alias.
* :func:`unheld_forms` — the concept plus every surface form the person does not
  hold: the words never printed as their skill until they tick the term.
* :func:`their_words` — what the card shows beside a term: held surface forms
  and the verified backing quote (only the person's text; a paraphrase is not a
  source).
* :func:`stamp_posting_terms_open` — the generation-seam stamp. It is pure: a
  ticked term is a vault skill or alias, so it drops out of the reading by itself.

Error direction: a reading that calls a posting term "held" leaves today's
behaviour (the term may be printed); a reading that calls a held word a posting
term asks the person one extra line. Every clause is closed and needle-directed.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

#: The row keys :func:`stamp_posting_terms_open` writes. Absent = no marker.
POSTING_TERM_OPEN_KEY = "posting_term_open"
UNHELD_FORMS_KEY = "posting_term_unheld_forms"

#: "Maschinendaten- und Betriebsdatenerfassung": a stem with a trailing hyphen,
#: a conjunction, then the compound whose tail completes the stem.
_SUSPENDED = re.compile(
    r"(\w+)-\s+(?:und|oder|bzw\.?|sowie|and|or|&|,)\s+(\w[\w-]*)", re.IGNORECASE
)
#: The shortest tail a suspended stem may borrow ("abschluss", "erfassung").
_MIN_TAIL = 4
#: A person's own acronym definition: "POC steht für Percentage of Completion",
#: "bei mir steht POC für …", "SLO stands for …".
_DEFINES = (
    re.compile(r"\b([A-ZÄÖÜ]{2,6})\s+(?:steht\s+für|stands\s+for|bedeutet|means)\s+([^,.;:()\n]{3,80})"),
    re.compile(r"\bsteht\s+([A-ZÄÖÜ]{2,6})\s+für\s+([^,.;:()\n]{3,80})"),
)
#: "Percentage of Completion (POC)": the initials of the words before the
#: bracket must spell the acronym.
_BRACKETED = re.compile(r"((?:[\wÄÖÜäöüß][\w-]*\s+){0,7}[\wÄÖÜäöüß][\w-]*)\s*\(\s*([A-ZÄÖÜ]{2,6})\s*\)")
#: A coordination inside one concept: "FastAPI / Django", "SAP & Excel", "A und B",
#: "Kafka, RabbitMQ", "Python oder Go", "FastAPI/Django". Names that carry the
#: same characters stay whole: an unspaced "&" ("R&D") and an unspaced "/"
#: between short or upper-case sides ("CI/CD", "SAP CO/FI", "TCP/IP", "UI/UX").
_COORDINATION = re.compile(r"\s+/\s+|\s*,\s+|\s+&\s+|\s+(?:und|and|oder|or)\s+", re.IGNORECASE)
#: An unspaced slash coordinates two NAMES only: each adjacent word has ≥ 4
#: letters and a lower-case letter ("FastAPI/Django", "Kafka/RabbitMQ").
_UNSPACED_SLASH = re.compile(r"(?<=\S)/(?=\S)")
_NAME_SIDE = re.compile(r"^(?=.*[a-zäöüß])[\wÄÖÜäöüß.+#-]{4,}$")


def _split_unspaced_slash(chunk: str) -> list[str]:
    pieces = _UNSPACED_SLASH.split(chunk)
    if len(pieces) < 2:
        return [chunk]
    left_words = [p.split()[-1] if p.split() else "" for p in pieces[:-1]]
    right_words = [p.split()[0] if p.split() else "" for p in pieces[1:]]
    if all(_NAME_SIDE.match(w) for w in left_words + right_words) and not any(
        w.endswith("-") for w in left_words
    ):
        return pieces
    return [chunk]


def coordination_parts(term: str) -> list[str]:
    """The parts of a coordinated concept (≥ 2), or ``[]`` when *term* is one
    name. The unspaced slash splits only between two word-like sides ("FastAPI/
    Django"), never inside an acronym pair ("CI/CD", "CO/FI", "TCP/IP")."""
    text = _nfc(term).strip()
    if not text or _SUSPENDED.search(text):
        return []  # "Monats- und Jahresabschluss": one word, read by clause (d)
    parts: list[str] = []
    for chunk in _COORDINATION.split(text):
        parts.extend(_split_unspaced_slash(chunk))
    parts = [p.strip() for p in parts if p and p.strip()]
    return parts if len(parts) >= 2 else []


def _nfc(text: Any) -> str:
    return unicodedata.normalize("NFC", str(text or ""))


def _initials_match(words: list[str], acronym: str) -> bool:
    upper = acronym.upper()
    every = "".join(w[0] for w in words).upper()
    capitals = "".join(w[0] for w in words if w[:1].isupper()).upper()
    return upper in (every, capitals)


def _person_acronyms(texts: list[str]) -> list[tuple[str, str]]:
    """``(acronym, expansion)`` pairs the person defined in their own text."""
    pairs: list[tuple[str, str]] = []
    for text in texts:
        for pattern in _DEFINES:
            for m in pattern.finditer(text):
                pairs.append((m.group(1), m.group(2).strip()))
        for m in _BRACKETED.finditer(text):
            acronym, words = m.group(2), m.group(1).split()
            for n in range(len(acronym), min(len(words), len(acronym) + 2) + 1):
                if _initials_match(words[-n:], acronym):
                    pairs.append((acronym, " ".join(words[-n:])))
                    break
    return pairs


def _suspended_expansions(texts: list[str]) -> set[str]:
    """Every completion a suspended stem can take from its partner compound,
    normalised ("maschinendaten" + "erfassung" from "…betriebsdatenerfassung").
    The tails are not split at a morpheme boundary, so junk words are generated
    too; a junk word matches only a term spelled exactly like it."""
    from applire.services.ats_audit import _norm

    out: set[str] = set()
    for text in texts:
        for m in _SUSPENDED.finditer(text):
            stem, partner = m.group(1), m.group(2).split("-")[0]
            for k in range(1, len(partner) - _MIN_TAIL + 1):
                out.add(_norm(stem + partner[k:]))
    return out


class _Reader:
    """Everything the reading needs about one vault, built once."""

    def __init__(self, profile_json: dict[str, Any]):
        from applire.services.keyword_ledger import (
            _draft_strings,
            _strip_denial_text,
            _strip_system_metadata,
            _strip_unclaimable,
            profile_literal_corpus,
        )
        from applire.services.profile.reconcile.stance import entry_is_claimable

        self.corpus = profile_literal_corpus(profile_json)
        raws = [_nfc(t) for t in _draft_strings(
            _strip_unclaimable(_strip_denial_text(_strip_system_metadata(profile_json)))
        )]
        meta = profile_json.get("metadata")
        self.denials = [
            _nfc(d.get("statement"))
            for d in ((meta.get("denied_concepts") if isinstance(meta, dict) else None) or [])
            if isinstance(d, dict) and isinstance(d.get("statement"), str)
        ]
        self.expanded = _suspended_expansions(raws + self.denials)
        self.acronyms = _person_acronyms(raws + self.denials)
        self.skill_names = [
            name
            for sk in profile_json.get("skills") or []
            if isinstance(sk, dict) and entry_is_claimable(sk)
            for name in [sk.get("name"), *(sk.get("aliases") or [])]
            if isinstance(name, str) and name.strip()
        ]

    def spellings(self, word: str) -> list[str]:
        from applire.services.keyword_ledger import _with_acronym_equivalents

        out = _with_acronym_equivalents([word])
        for acronym, expansion in self.acronyms:
            pattern = re.compile(r"(?<!\w)" + re.escape(acronym) + r"(?!\w)")
            if pattern.search(word):
                out.append(pattern.sub(expansion, word))
        return out

    def _present(self, word: str) -> bool:
        from applire.services.ats_audit import _norm, surface_present_whole_token

        return surface_present_whole_token(word, self.corpus) or _norm(word) in self.expanded

    def held(self, word: str) -> bool:
        """Is *word* the person's own word (module docstring, clauses a–g)?"""
        from applire.services.ats_audit import skills_near_dupe
        from applire.services.profile.reconcile.skill_names import (
            _claimed_phrase,
            _sentences,
            _words,
        )

        word = _nfc(word).strip()
        if not word:
            return True  # nothing to ask about
        for spelling in self.spellings(word):
            if self._present(spelling):
                return True
            needle = [w for w, _s, _e in _words(spelling)]
            for statement in self.denials:
                if needle and _claimed_phrase(_words(statement), needle, _sentences(statement)):
                    return True
        if any(skills_near_dupe(word, name) for name in self.skill_names):
            return True
        if self._suspended_term_held(word):
            return True
        parts = coordination_parts(word)
        return bool(parts) and all(self.held(p) for p in parts)

    def _suspended_term_held(self, word: str) -> bool:
        """A term written with a suspended hyphen ("Monats- und Jahresabschluss-
        Reporting"): its completed stem is held and every other word is held."""
        from applire.services.ats_audit import surface_present_whole_token

        m = _SUSPENDED.search(word)
        if not m:
            return False
        stem, partner = m.group(1), m.group(2).split("-")[0]
        stem_held = any(
            self._present(stem + partner[k:])
            for k in range(1, len(partner) - _MIN_TAIL + 1)
        )
        rest = [w for w in re.split(r"[\s\-/]+", word[m.start(2):]) if w]
        return stem_held and bool(rest) and all(
            surface_present_whole_token(w, self.corpus) for w in rest
        )


def _reads_row(entry: Any) -> bool:
    from applire.services.keyword_ledger import (
        is_aim_only,
        is_jd_keyword_only,
        is_positioning_only,
        is_scope_entry,
    )

    return (
        isinstance(entry, dict)
        and entry.get("claimable") is True
        and entry.get("status") == "direct"
        and isinstance(entry.get("concept"), str) and bool(entry["concept"].strip())
        and not is_positioning_only(entry) and not is_scope_entry(entry)
        and not is_aim_only(entry) and not is_jd_keyword_only(entry)
    )


def posting_term_rows(
    ledger: list[dict[str, Any]] | None, profile_json: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The posting-term rows of *ledger* against *profile_json* — computed, never
    read from a stamp. ``[]`` without a vault (nothing is known to be absent)."""
    if not ledger or not isinstance(profile_json, dict):
        return []
    reader = _Reader(profile_json)
    if not reader.corpus:
        return []
    return [e for e in ledger if _reads_row(e) and not reader.held(e["concept"])]


def is_offerable(row: dict[str, Any], profile_json: dict[str, Any] | None, reader: Any = None) -> bool:
    """May the card (and the agent) offer *row*'s concept as ONE tick? Not a
    coordination that mixes a held part with an unheld one ("FastAPI / Django"
    for a person who wrote only Django): the person's own word covers one part,
    so the profile does not show the other, and a tick would write a skill that
    attests it. Such a row stays stamped open — never a chip — and is simply not
    asked. A one-name term, or a coordination of parts none of which is held, is
    offered like any term."""
    parts = coordination_parts(row.get("concept") or "")
    if not parts:
        return True
    reader = reader or _Reader(profile_json or {})
    return not any(reader.held(p) for p in parts)


def offered_posting_term_rows(
    ledger: list[dict[str, Any]] | None, profile_json: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The posting-term rows the card and the agent put to the person:
    :func:`posting_term_rows` minus the mixed coordinations (:func:`is_offerable`)."""
    rows = posting_term_rows(ledger, profile_json)
    if not rows:
        return []
    reader = _Reader(profile_json or {})
    return [r for r in rows if is_offerable(r, profile_json, reader)]


def unheld_forms(row: dict[str, Any], profile_json: dict[str, Any] | None) -> list[str]:
    """The concept plus every surface form of *row* the person does not hold —
    the words never printed as their skill while the term is open."""
    reader = _Reader(profile_json or {})
    out: list[str] = []
    concept = row.get("concept")
    if isinstance(concept, str) and concept.strip():
        out.append(concept)
    for form in row.get("surface_forms") or []:
        if isinstance(form, str) and form.strip() and form not in out and not reader.held(form):
            out.append(form)
    return out


def their_words(row: dict[str, Any], profile_json: dict[str, Any] | None) -> list[str]:
    """The person's own words beside a term on the card: held surface forms, then
    the row's verified backing quote. Never the classifier's ``evidence`` (a
    paraphrase is not a source)."""
    reader = _Reader(profile_json or {})
    out: list[str] = []
    for form in row.get("surface_forms") or []:
        if isinstance(form, str) and form.strip() and form not in out and reader.held(form):
            out.append(form)
    backing = row.get("backing")
    quote = backing.get("quote") if isinstance(backing, dict) else None
    if isinstance(quote, str) and quote.strip() and quote not in out:
        out.append(quote.strip())
    return out


def is_posting_term_open(entry: dict[str, Any] | None) -> bool:
    """A row :func:`stamp_posting_terms_open` marked."""
    return isinstance(entry, dict) and entry.get(POSTING_TERM_OPEN_KEY) is True


def stamped_unheld_forms(entry: dict[str, Any] | None) -> list[str]:
    """The unheld forms a stamped row carries ([] for an unstamped row)."""
    if not is_posting_term_open(entry):
        return []
    forms = entry.get(UNHELD_FORMS_KEY) if isinstance(entry, dict) else None
    return [f for f in forms or [] if isinstance(f, str) and f.strip()]


def stamp_posting_terms_open(
    ledger: list[dict[str, Any]] | None,
    profile_json: dict[str, Any] | None,
    *,
    same_language: bool,
) -> list[dict[str, Any]]:
    """Stamp every posting-term row with :data:`POSTING_TERM_OPEN_KEY` and its
    :data:`UNHELD_FORMS_KEY`, and drop a stale stamp from every other row. Across
    languages nothing is stamped (ADR-048 am. cl. 2). Pure."""
    rows = posting_term_rows(ledger, profile_json) if same_language else []
    hits = {id(e) for e in rows}
    out: list[dict[str, Any]] = []
    for e in ledger or []:
        if not isinstance(e, dict):
            out.append(e)
        elif id(e) in hits:
            out.append({**e, POSTING_TERM_OPEN_KEY: True, UNHELD_FORMS_KEY: unheld_forms(e, profile_json)})
        elif POSTING_TERM_OPEN_KEY in e or UNHELD_FORMS_KEY in e:
            out.append({k: v for k, v in e.items() if k not in (POSTING_TERM_OPEN_KEY, UNHELD_FORMS_KEY)})
        else:
            out.append(e)
    return out


def chip_posting_term(chip: str, ledger: list[dict[str, Any]] | None) -> str | None:
    """The concept of the stamped row whose unheld form *chip* IS (normalised
    equality), or ``None``."""
    from applire.services.ats_audit import _norm

    if not isinstance(chip, str) or not chip.strip():
        return None
    key = _norm(chip)
    for e in ledger or []:
        if any(_norm(f) == key for f in stamped_unheld_forms(e)):
            return e.get("concept")
    return None


def language_pair_reading(
    profile_json: dict[str, Any] | None, document_language: str | None
) -> dict[str, Any]:
    """The pair fact the list is gated on, with its counts (no profile text).

    Same as the Oracle's ``holds_other_language`` (ADR-068: the vault's dominant
    language, then the share of its long units in the other language, threshold
    ``OTHER_LANGUAGE_MIN_SHARE``) with ONE difference, measured on every captured
    vault (WP-Q10 fix round): a long unit casts a language vote only on positive
    evidence. ``detect_language`` breaks a tie toward German, so an English noun
    phrase with no function word ("AWS Certified Solutions Architect –
    Associate", "Keeping approximately 40k logistics users' shipments flowing")
    counted as German and took an English vault to 0.214 — the list vanished. A
    unit whose scores tie votes for neither language here.

    ``same`` is False on an unknown language or a missing vault: fail CLOSED (no
    list), since a list on an unknown pair could suppress a faithful translation.
    """
    reading: dict[str, Any] = {
        "same": False, "reason": "unknown_language", "document_language": document_language,
        "dominant_language": None, "long_units": 0, "decided_units": 0, "other_share": None,
    }
    if not isinstance(profile_json, dict) or document_language not in ("de", "en"):
        return reading
    from applire.services.oracle.matchers.vault import (
        OTHER_LANGUAGE_MIN_SHARE,
        OTHER_LANGUAGE_MIN_WORDS,
        build_vault_index,
    )
    from applire.utils.language_detection import language_scores

    try:
        index = build_vault_index(profile_json)
    except Exception:  # pragma: no cover — defensive: no list, today's behaviour
        reading["reason"] = "index_failed"
        return reading
    reading["dominant_language"] = index.dominant_language
    if index.dominant_language != document_language:
        reading["reason"] = "dominant_language_differs"
        return reading
    other = "en" if document_language == "de" else "de"
    long_units = [u.text for u in index.units if len(u.text.split()) >= OTHER_LANGUAGE_MIN_WORDS]
    votes = []
    for text in long_units:
        de, en = language_scores(text)
        if de != en:
            votes.append("de" if de > en else "en")
    share = votes.count(other) / len(votes) if votes else 0.0
    reading.update(long_units=len(long_units), decided_units=len(votes), other_share=round(share, 3))
    if share >= OTHER_LANGUAGE_MIN_SHARE:
        reading["reason"] = "mixed_vault"
        return reading
    reading.update(same=True, reason="same_language")
    return reading


def same_language_pair(
    profile_json: dict[str, Any] | None,
    document_language: str | None,
    *,
    seam: str | None = None,
    job_id: Any = None,
) -> bool:
    """True when the vault states its evidence in *document_language*
    (:func:`language_pair_reading`). When the gate withholds the list, one INFO
    line says so — ids, languages and counts only, never profile text — so a
    suppression is countable in the log instead of silent."""
    reading = language_pair_reading(profile_json, document_language)
    if not reading["same"] and seam is not None:
        logger.info(
            "posting terms: list withheld seam=%s job=%s reason=%s doc=%s dominant=%s "
            "long_units=%s decided=%s other_share=%s",
            seam, job_id, reading["reason"], reading["document_language"],
            reading["dominant_language"], reading["long_units"], reading["decided_units"],
            reading["other_share"],
        )
    return reading["same"]


def stamp_for_generation(
    ledger: list[dict[str, Any]] | None,
    profile_json: dict[str, Any] | None,
    document_language: str | None,
    *,
    seam: str = "generation",
    job_id: Any = None,
) -> list[dict[str, Any]]:
    """THE generation-seam call (CV and letter): :func:`stamp_posting_terms_open`
    with the pair's language fact."""
    same = same_language_pair(profile_json, document_language, seam=seam, job_id=job_id)
    return stamp_posting_terms_open(ledger, profile_json, same_language=same)
