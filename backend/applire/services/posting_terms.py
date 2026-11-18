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


# ── twin skills on the CV (ruling Q10-8, founder 2026-11-17) ────────────────
#
# A ticked term whose card showed one of the person's own skill NAMES as "their
# words" stands in for that skill on THIS posting's CV: the CV prints the ticked
# term and leaves that one skill out. The profile keeps both; other postings'
# CVs are untouched. Bound structurally: the confirmation records, at answer
# time, the exact skill names the card showed (``shown_skills``); composition
# reads that record through the ledger stamp below — no matching of words at
# composition time beyond the closed name key.

#: The row key :func:`stamp_twin_skills` writes on the ticked term's ledger row:
#: ``[{"skill": <left out>, "term": <printed>, "holder": <skill carrying the term>}]``.
IN_PLACE_OF_KEY = "posting_term_in_place_of"


def name_key(value: Any) -> str:
    """The closed name key (unicode NFKC, whitespace, case) — nothing semantic."""
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def card_skill_names(
    words: list[str] | None, skills: list[Any] | None, *, holder: str | None = None
) -> list[str]:
    """The profile skill names *words* (a term's "their words" on the card) show
    EXACTLY (name key), in the skill's stored spelling, minus *holder* — the skill
    the confirmation itself lands on (a term already a skill name, or the skill it
    became a second name of)."""
    by_key: dict[str, str] = {}
    for s in skills or []:
        name = s.get("name") if isinstance(s, dict) else None
        if isinstance(name, str) and name.strip():
            by_key.setdefault(name_key(name), name.strip())
    out: list[str] = []
    for w in words or []:
        hit = by_key.get(name_key(w)) if isinstance(w, str) else None
        if hit and name_key(hit) != name_key(holder) and hit not in out:
            out.append(hit)
    return out


def _job_named(job_dict: dict[str, Any] | None) -> set[str]:
    out: set[str] = set()
    for fld in ("required_skills", "nice_to_have_skills", "keywords"):
        for v in (job_dict or {}).get(fld) or []:
            if isinstance(v, str) and v.strip():
                out.add(name_key(v))
    return out


def twin_pairs_for_posting(
    profile_json: dict[str, Any] | None,
    job_id: Any,
    ledger: list[dict[str, Any]] | None,
    *,
    job_dict: dict[str, Any] | None = None,
    pins: Any = (),
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """The (skill left out, term printed) pairs for THIS posting's CV, and the
    count of shown skills KEPT per reason. Read from the confirmation record:
    only a confirmation for *job_id* that recorded ``shown_skills``.

    A shown skill is kept (no pair) when:
    - ``posting_names`` — the posting names it itself: a ledger row's concept or
      an entry of the posting's skill/keyword lists equals it;
    - ``pinned`` — the person pinned it for the CV (ADR-077);
    - ``ticked`` — it is itself a term confirmed for this posting (a ticked term
      is always printed; this also breaks any chain or cycle of stand-ins);
    - ``gone`` — the profile no longer holds it as a claimable skill.
    A skill shown for two ticked terms is left out ONCE (the first term)."""
    from applire.services.profile.reconcile.stance import claimable_skill_names

    kept = {"posting_names": 0, "pinned": 0, "ticked": 0, "gone": 0}
    if not isinstance(profile_json, dict) or job_id is None:
        return [], kept
    job = str(job_id)
    skills = [s for s in profile_json.get("skills") or [] if isinstance(s, dict)]
    confs: list[tuple[dict[str, Any], dict[str, Any]]] = [
        (s, c) for s in skills for c in s.get("confirmations") or []
        if isinstance(c, dict) and str(c.get("job_id") or "") == job
    ]
    if not confs:
        return [], kept
    ticked = {name_key(c.get("name")) for _s, c in confs} | {name_key(s.get("name")) for s, _c in confs}
    claimable = {name_key(n) for n in claimable_skill_names(profile_json)}
    named = _job_named(job_dict)
    for e in ledger or []:
        if isinstance(e, dict) and isinstance(e.get("concept"), str):
            named.add(name_key(e["concept"]))
    pinned: frozenset[str] = frozenset()
    if pins:
        try:
            from applire.services.pin_reach import pinned_skill_quote_norms, skill_tag_is_pinned

            pinned = pinned_skill_quote_norms(pins)
        except Exception:  # pragma: no cover — defensive: no pin set, no pin exception
            logger.exception("posting terms: twin rule could not read pins")
    pairs: list[dict[str, str]] = []
    left: set[str] = set()
    for holder, conf in confs:
        term = str(conf.get("name") or "").strip()
        if not term:
            continue
        for skill in conf.get("shown_skills") or []:
            if not isinstance(skill, str) or not skill.strip():
                continue
            k = name_key(skill)
            if k in left:
                continue
            if k in ticked:
                kept["ticked"] += 1
            elif k not in claimable:
                kept["gone"] += 1
            elif k in named:
                kept["posting_names"] += 1
            elif pinned and skill_tag_is_pinned(skill, pinned):
                kept["pinned"] += 1
            else:
                left.add(k)
                pairs.append({"skill": skill.strip(), "term": term,
                              "holder": str(holder.get("name") or term).strip()})
    return pairs, kept


def stamp_twin_skills(
    ledger: list[dict[str, Any]] | None,
    profile_json: dict[str, Any] | None,
    job_id: Any,
    *,
    job_dict: dict[str, Any] | None = None,
    pins: Any = (),
    seam: str = "cv generation",
) -> list[dict[str, Any]]:
    """THE CV-seam stamp: each pair rides on the ledger row of its term (concept,
    else a surface form, by name key), so the writer's and reviewer's blocks, the
    skills checks and the compose all read one record. A stale stamp is dropped.
    A pair with no row of its term is not applied (counted in the INFO line)."""
    pairs, kept = twin_pairs_for_posting(profile_json, job_id, ledger, job_dict=job_dict, pins=pins)
    out: list[dict[str, Any]] = []
    for e in ledger or []:
        if isinstance(e, dict) and IN_PLACE_OF_KEY in e:
            e = {k: v for k, v in e.items() if k != IN_PLACE_OF_KEY}
        out.append(e)
    placed = 0
    for pair in pairs:
        t = name_key(pair["term"])
        at = next((i for i, e in enumerate(out)
                   if isinstance(e, dict) and name_key(e.get("concept")) == t), None)
        if at is None:
            at = next((i for i, e in enumerate(out) if isinstance(e, dict) and any(
                name_key(f) == t for f in e.get("surface_forms") or [] if isinstance(f, str))), None)
        if at is None:
            continue
        row = out[at]
        out[at] = {**row, IN_PLACE_OF_KEY: [*(row.get(IN_PLACE_OF_KEY) or []), dict(pair)]}
        placed += 1
    if pairs or any(kept.values()):
        logger.info(
            "posting terms: twin skills seam=%s job=%s left_out=%d unplaced=%d kept_posting_names=%d "
            "kept_pinned=%d kept_ticked=%d kept_gone=%d",
            seam, job_id, placed, len(pairs) - placed, kept["posting_names"], kept["pinned"],
            kept["ticked"], kept["gone"],
        )
    return out


def twin_pairs(ledger: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """The pairs :func:`stamp_twin_skills` put on *ledger* (generation order)."""
    out: list[dict[str, str]] = []
    for e in ledger or []:
        for p in (e.get(IN_PLACE_OF_KEY) if isinstance(e, dict) else None) or []:
            if isinstance(p, dict) and isinstance(p.get("skill"), str) and isinstance(p.get("term"), str):
                out.append(p)
    return out


def twin_left_out_keys(ledger: list[dict[str, Any]] | None) -> set[str]:
    """Name keys of the skills this posting's CV leaves out."""
    return {name_key(p["skill"]) for p in twin_pairs(ledger)}


def settle_twin_skills(
    skills: list[Any] | None, pairs: list[dict[str, str]]
) -> tuple[list[Any], list[tuple[str, str, str]]]:
    """Pure: the skills list with each left-out skill settled — dropped when its
    term (or the skill carrying it) is on the list, else REPLACED by the term in
    its slot, so the competence is never printed by neither. Returns the list and
    ``(skill, term, "dropped" | "replaced")`` per settle."""
    out = list(skills or [])
    applied: list[tuple[str, str, str]] = []
    for pair in pairs:
        x = name_key(pair["skill"])
        present = {name_key(pair["term"]), name_key(pair.get("holder") or pair["term"])}
        idx = [i for i, c in enumerate(out) if isinstance(c, str) and name_key(c) == x]
        if not idx:
            continue
        on_page = any(isinstance(c, str) and name_key(c) in present for c in out)
        first = idx[0]
        if not on_page:
            out[first] = pair["term"]
            applied.append((pair["skill"], pair["term"], "replaced"))
            idx = idx[1:]
        else:
            applied.append((pair["skill"], pair["term"], "dropped"))
        for i in reversed(idx):
            del out[i]
    return out, applied
