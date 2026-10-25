# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""E-2 (founder ruling, Hazelnut build 2; ADR-052 amended 2026-10-22, WP-T): a
stated qualification or title the vault never backs is ``unbacked``.

The build-2 delivery run graded an agent-door letter sentence "Als ausgebildete
Bilanzbuchhalterin …" ``unverifiable`` against a vault that holds no such
qualification (F-E). Degrees, regulated professional titles and named
certifications are the claims a recruiter checks first and a candidate can least
afford to over-state; "no close evidence" is the wrong colour for them.

The rule, deterministic and closed:

* **Detection** — a closed family list (:data:`_FAMILIES`) matched on the claim
  text. Upper-case acronyms (``MBA``, ``ALS``, ``PMP``) are matched case-SENSITIVELY
  ("als" is a German preposition). A professional TITLE (``Bilanzbuchhalterin``,
  ``Registered Nurse``) counts only where the sentence ascribes it to the
  candidate — a self-ascription frame ("bin", "als", "I am a", "as a",
  "ausgebildete") or, in a CV, the head of the line. "betreue Wirtschaftsprüfer"
  ascribes nothing.
* **Skips** — hedged, negated or aspired sentences ("strebe … an", "in
  Ausbildung", "working towards", "kein …"), and letter sentences without a
  first-person marker (a sentence about the employer or the role).
* **Backing** — lenient on purpose: ANY form of the family anywhere in the
  claimable vault text backs the mention ("Dipl.-Kfm." backs "Diplom-Kaufmann").
  The check withdraws an endorsement only where the vault is silent about the
  whole family; it never judges a grade, a year or a field of study.

Measured before it shipped (2026-10-22, 1,219 delivered documents + vaults of the
build-2 corpus, synthetic personas): 186 credential mentions, 0 unbacked — 0 false
reds; the F-E sentence is unbacked against all four case vaults. The caller
(:func:`applire.services.oracle.audit.verify_claim`) also never applies the rule on
a pair whose vault may state its evidence in another language (``holds_other_language``)
— a German title against an English vault is not checked across languages.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

DocumentKind = Literal["cv", "letter"]


def fold(text: str) -> str:
    """Case-, hyphen- and slash-insensitive form used for BACKING only."""
    s = unicodedata.normalize("NFKC", text or "").replace("­", "")
    s = re.sub(r"[-‐-―−/]", " ", s)
    return re.sub(r"\s+", " ", s).lower().strip()


_G = r"(?:in)?"  # singular, m/f — a plural ("Steuerberatern") is never the candidate
#: A-2 (WP-FO): a chamber qualifier after a title — "(IHK)", "(HWK)", "IHK"…
_CHAMBER = r"\s*\(?\s*(?:ihk|hwk|handwerkskammer|industrie- und handelskammer)\b\)?"

#: (family, claim regex — case-INsensitive, backing forms in folded vault text).
#: ``None`` backing = the qualified noun after "certified/zertifizierte" must appear.
_FAMILIES: tuple[tuple[str, str, tuple[str, ...] | None], ...] = (
    # degrees
    ("bachelor", r"\b(?:bachelor(?:'s)?|b\.\s?sc\.?|b\.\s?a\.|b\.\s?eng\.?|bsc)\b",
     ("bachelor", "b sc", "b.sc", "bsc", "b.a.", "b a ", "b.eng", "b eng")),
    ("master", r"\b(?:master(?:'s)?\s+(?:of|in|degree|abschluss|studium)\b|master[- ]?abschluss|"
               r"m\.\s?sc\.?|m\.\s?a\.|m\.\s?eng\.?|msc|ll\.\s?m\.?)",
     ("master", "m sc", "m.sc", "msc", "m.a.", "m a ", "m.eng", "m eng", "ll.m", "ll m")),
    ("doctorate", r"\b(?:ph\.?\s?d\.?|doktor(?:titel|arbeit)?|promoviert\w*|promotion\b|doctorate)\b",
     ("phd", "ph d", "ph.d", "doktor", "promotion", "promoviert", "doctorate", "dr. ", "dr ")),
    ("diplom", r"\b(?:diplom\w*|dipl\.\s?-?\s?\w+)", ("diplom", "dipl")),
    ("staatsexamen", r"\bstaatsexamen\b", ("staatsexamen",)),
    # regulated / credentialed professional titles (DE)
    ("bilanzbuchhalter", r"\bbilanzbuchhalter" + _G + r"\b", ("bilanzbuchhalter",)),
    ("steuerberater", r"\bsteuerberater" + _G + r"\b", ("steuerberater",)),
    ("wirtschaftspruefer", r"\bwirtschaftsprüfer" + _G + r"\b", ("wirtschaftsprüfer",)),
    ("rechtsanwalt", r"\b(?:rechtsanw[aä]lt" + _G + r"|fachanw[aä]lt" + _G + r")\b", ("rechtsanw", "fachanw")),
    ("apotheker", r"\bapotheker" + _G + r"\b", ("apotheker",)),
    ("facharzt", r"\bfach[aä]rzt" + _G + r"\b", ("facharzt", "fachärzt")),
    ("approbation", r"\bapprobi\w*|\bapprobation\b", ("approb",)),
    ("industriemeister",
     r"\b(?:industrie|handwerks|elektro|metallbauer|feinwerk)meister" + _G + r"\b|\bmeister(?:brief|prüfung|titel)\b",
     ("industriemeister", "handwerksmeister", "elektromeister", "metallbauermeister", "feinwerkmeister",
      "meisterbrief", "meisterprüfung", "meistertitel", "meister")),
    # A-2 (WP-FO, 2026-10-23): any trade's master title once a chamber names it
    # ("Kfz-Mechatronikermeister (HWK)"). An open family: the trade's own title
    # must appear in the vault — "Industriemeister" backs no Kfz master title.
    ("meister_kammer", r"\b(?!(?:industrie|handwerks|elektro|metallbauer|feinwerk)meister)[\wäöüß-]+meister"
     + _G + _CHAMBER, None),
    ("fachwirt", r"\b\w*fachwirt" + _G + r"\b", ("fachwirt",)),
    ("betriebswirt_ihk", r"\b(?:staatlich geprüfte?r?|geprüfte?r?|technische?r?)\s+betriebswirt" + _G + r"\b"
     r"|\bbetriebswirt" + _G + _CHAMBER, ("betriebswirt",)),
    # A-2 (WP-FO, 2026-10-23): chamber qualifications whose bare word is also a
    # job title — counted only with the chamber named, backed only by a vault
    # that names the chamber form too (a "Controllerin" job title backs nothing).
    ("controller_ihk", r"\bcontroller" + _G + _CHAMBER + r"|\b(?:ihk[- ])?geprüfte?r?\s+controller" + _G + r"\b",
     ("controller (ihk)", "controllerin (ihk)", "controller ihk", "controllerin ihk", "ihk controller",
      "geprüfter controller", "geprüfte controllerin")),
    ("kaufmann_ihk", r"\b[\wäöüß]+kauf(?:mann|frau)\b", None),
    ("techniker_sg", r"\bstaatlich geprüfte?r?\s+techniker" + _G + r"\b", ("techniker",)),
    ("fasi", r"\bfachkraft für arbeitssicherheit\b|\bsicherheitsfachkraft\b|\bsifa\b",
     ("fachkraft für arbeitssicherheit", "sicherheitsfachkraft", "sifa")),
    ("ada", r"\bausbildereignung\w*|\bada[- ]schein\b|\bausbilderschein\b|\bausbilder(?:in)?\s*\(aevo\)",
     ("ausbildereignung", "ada schein", "ada", "ausbilderschein", "aevo")),
    ("pflegefachkraft",
     r"\b(?:pflegefach(?:kraft|frau|mann)|gesundheits- und krankenpfleger" + _G + r"|krankenschwester|"
     r"fachkrankenpfleger" + _G + r")\b",
     ("pflegefach", "krankenpfleg", "krankenschwester", "nurse", "nursing")),
    ("notfallsanitaeter", r"\bnotfallsanitäter" + _G + r"\b", ("notfallsanitäter",)),
    # EN credentials / certifications
    ("chartered_acc", r"\bchartered accountant\b", ("chartered accountant",)),
    ("prince2", r"\bprince ?2\b", ("prince2", "prince 2")),
    ("six_sigma", r"\b(?:green|black|yellow)\s?belt\b|\bsix sigma\b", ("belt", "six sigma")),
    ("scrum_phrase", r"\bcertified scrum (?:master|product owner)\b|\bsafe agilist\b", ("scrum", "safe")),
    ("registered_nurse", r"\bregistered nurse\b|\b(?:nmc|rn)\s+registration\b",
     ("registered nurse", "nmc", "registration", "nurse")),
    ("life_support_phrase", r"\b(?:advanced|basic|immediate) life support\b",
     ("life support", "acls", "bls", "als", "ils", "pals")),
    ("certified_x",
     r"\b(?:certified|licensed|chartered|zertifizierte?r?|lizenzierte?r?)\s+(?!in\b|by\b|for\b)"
     r"([a-zäöüß][\w-]*(?:\s+[a-zäöüß][\w-]*)?)",
     None),
)

#: Upper-case acronyms, matched case-SENSITIVELY.
_ACRONYMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("mba", r"\bMBA\b|\b[Mm]aster of [Bb]usiness [Aa]dministration\b", ("mba", "master of business administration")),
    ("cpa", r"\b(?:CPA|ACCA|CIMA)\b", ("cpa", "acca", "cima")),
    ("cfa", r"\bCFA\b", ("cfa",)),
    ("pmp", r"\b(?:PMP|PRINCE2|IPMA)\b", ("pmp", "prince2", "ipma")),
    ("security_cert", r"\b(?:CISSP|CISA|CISM|CEH|OSCP)\b", ("cissp", "cisa", "cism", "ceh", "oscp")),
    ("itil", r"\bITIL\b", ("itil",)),
    ("ada_aevo", r"\bAEVO\b", ("aevo", "ausbildereignung", "ada schein", "ausbilderschein")),
    ("scrum_cert", r"\b(?:PSM(?: ?I{1,3})?|CSM|CSPO|PSPO)\b", ("psm", "csm", "cspo", "pspo", "scrum")),
    ("life_support", r"\b(?:ACLS|BLS|ALS|ILS|PALS)\b", ("life support", "acls", "bls", "als", "ils", "pals")),
)

_COMPILED: tuple[tuple[str, re.Pattern[str], tuple[str, ...] | None], ...] = tuple(
    (n, re.compile(r, re.I), b) for n, r, b in _FAMILIES
) + tuple((n, re.compile(r), b) for n, r, b in _ACRONYMS)

_FIRST_PERSON = re.compile(r"\b(?:ich|mein\w*|mir|mich|i|my|me|i'm|i've)\b", re.I)
_HEDGE = re.compile(
    r"\b(?:kein\w*|nicht|ohne|no|not|without|never|nie|weder|angestrebt\w*|strebe|anstreb\w*|"
    r"in ausbildung|berufsbegleitend|derzeit absolviere|bereite (?:ich )?mich|vorbereitung auf|working towards|"
    r"work towards|pursuing|currently studying|plan to|planning to|preparing for|enrolled)\b",
    re.I,
)

#: Professional TITLES (not degrees or certifications): accusable only where the
#: sentence ascribes the title to the candidate.
_TITLE_FAMILIES = frozenset({
    "bilanzbuchhalter", "steuerberater", "wirtschaftspruefer", "rechtsanwalt", "apotheker",
    "facharzt", "industriemeister", "fachwirt", "pflegefachkraft", "notfallsanitaeter",
    "registered_nurse", "chartered_acc", "controller_ihk", "kaufmann_ihk", "meister_kammer",
})
_FRAME = re.compile(
    r"(?:\b(?:ich bin|bin ich|bin|als|zum|zur|ausgebildete?r?|gelernte?r?|geprüfte?r?|examinierte?r?|"
    r"staatlich (?:geprüfte?r?|anerkannte?r?)|i am an?|i'm an?|as an?|qualified|work(?:ed|ing)? as an?|"
    # A-2 (WP-FO): "führe den Titel …", "trage die Berufsbezeichnung …", "the title of"
    r"titel|berufsbezeichnung|bezeichnung|title(?: of)?)"
    r"\s+(?:[\w.-]+\s+){0,2})$",
    re.I,
)
#: CV register: a title at the HEAD of a line, after at most one adjective.
_CV_HEAD = re.compile(
    r"(?:^|[.;:!?]\s+)(?:(?:[\w-]+(?:e|er|es|en)|senior|experienced|certified|qualified)\s+)?$", re.I
)


@dataclass(frozen=True)
class CredentialMention:
    family: str
    #: The text as written in the claim (shown to the user).
    matched: str


def _ascribed(text: str, start: int, kind: DocumentKind) -> bool:
    before = text[:start]
    return bool(_FRAME.search(before) or (kind == "cv" and _CV_HEAD.search(before)))


def detect_credentials(text: str, *, kind: DocumentKind) -> list[CredentialMention]:
    """Credential mentions the claim ascribes to the candidate (one per family)."""
    if not text:
        return []
    if kind == "letter" and not _FIRST_PERSON.search(text):
        return []
    if _HEDGE.search(text):
        return []
    out: list[CredentialMention] = []
    seen: set[str] = set()
    for name, rx, backing in _COMPILED:
        if name in seen:
            continue
        for m in rx.finditer(text):
            if name in _TITLE_FAMILIES and not _ascribed(text, m.start(), kind):
                continue
            out.append(CredentialMention(name, m.group(0).strip()))
            if backing is not None:
                # A closed family: one mention decides it (lenient family backing).
                seen.add(name)
                break
            # An open family (``certified_x``, ``kaufmann_ihk`` — A-2, WP-FO): each
            # mention names its own qualification and is backed on its own.
    return out


def is_backed(mention: CredentialMention, vault_folded: str) -> bool:
    """Lenient: any form of the family anywhere in the folded claimable vault."""
    for name, _rx, forms in _COMPILED:
        if name != mention.family:
            continue
        if forms is None and name == "meister_kammer":
            title = re.sub(r"(?:in)?\s*\(?\s*(?:ihk|hwk|handwerkskammer|industrie- und handelskammer)\)?\s*$", "",
                           fold(mention.matched))
            return title.strip() in vault_folded
        if forms is None and name == "kaufmann_ihk":
            # A-2: the trade's own stem must appear ("industriekauffrau" is backed by
            # "Industriekaufmann", not by any other "…kaufmann").
            stem = re.sub(r"(?:mann|frau)$", "", fold(mention.matched))
            return stem in vault_folded
        if forms is None:  # certified_x: the qualified noun must appear
            m = re.search(r"\s+(.+)$", fold(mention.matched))
            noun = m.group(1) if m else fold(mention.matched)
            return noun.split()[0] in vault_folded
        return any(fold(f) in vault_folded or f in vault_folded for f in forms)
    return True


def unbacked_credentials(text: str, *, kind: DocumentKind, vault_folded: str) -> list[CredentialMention]:
    """The credential mentions of ``text`` no form in the vault backs."""
    return [m for m in detect_credentials(text, kind=kind) if not is_backed(m, vault_folded)]


def kind_for_location(location: str) -> DocumentKind:
    """CV claims carry CV locations (summary/work_history/skills/projects/…); a
    letter's carry ``body.…`` and an external text's ``text[…]`` — both read in the
    stricter letter register (first person required)."""
    head = (location or "").split("[", 1)[0].split(".", 1)[0]
    return "letter" if head in {"body", "text"} else "cv"


# ── ADR-052 amended 2026-10-23 (ruling B3-9, WP-FO): structured ENTRY claims ──
#
# A CV's ``certifications[i].name`` / ``education[i].degree`` is a credential the
# document states outright — no frame, no hedge, no first person: the entry IS the
# ascription. It is backed only when a CLAIMABLE vault entry (certification name,
# education degree or one of its ``degree_aliases``) NAMES it: identity under the
# entry fold, a ≥ 2-token shortening of a vault name, an acronym the VAULT entry
# gives in parentheses, or a fixed degree abbreviation. Never by "the same
# credential family": a family groups DIFFERENT credentials (CPA/ACCA/CIMA,
# "Industriemeister Metall"/"…Elektrotechnik", any "…kaufmann") — as an entry
# identity it would ground a fabricated entry beside any real one (main review of
# 84b8b229, confirmed on 7 shapes before the fix).

_ENTRY_STRIP = re.compile(r"[®™©]")
_ENTRY_PUNCT = re.compile(r"[.,;:()\[\]{}\"'„“”‚‘’/]")  # dashes: fold() already maps them
_ENTRY_PAREN = re.compile(r"\(([^)]*)\)|\[([^\]]*)\]")
#: A parenthetical that QUALIFIES the name (chamber, school type) is no name of
#: its own: "(IHK)" must not make every IHK entry the same credential.
_ENTRY_QUALIFIERS = frozenset({"ihk", "hwk", "fh", "th", "tu", "uas", "dhbw", "ba", "hs", "m w d"})
#: Families whose match is a phrase a translation rewrites — not language-neutral.
_ENTRY_TRANSLATABLE = frozenset({"certified_x"})
#: Fixed abbreviation ↔ long form of the same degree (folded, punctuation-free).
_ENTRY_SYNONYMS = {
    "mba": "master of business administration",
    "bsc": "bachelor of science", "b sc": "bachelor of science",
    "msc": "master of science", "m sc": "master of science",
    "b a": "bachelor of arts", "m a": "master of arts",
    "b eng": "bachelor of engineering", "m eng": "master of engineering",
    "beng": "bachelor of engineering", "meng": "master of engineering",
    "llb": "bachelor of laws", "ll b": "bachelor of laws", "llm": "master of laws", "ll m": "master of laws",
    "phd": "doctor of philosophy", "ph d": "doctor of philosophy",
}


#: Abbreviation → long form at the START of a degree name ("B.Sc. Informatik" ↔
#: "Bachelor of Science Informatik", "Dipl.-Kfm." ↔ "Diplom-Kaufmann"). Token-wise:
#: the abbreviation is a fixed spelling of the same degree, never a family.
_ENTRY_PREFIXES: tuple[tuple[tuple[str, ...], str], ...] = tuple(
    (tuple(k.split()), v) for k, v in {
        "b sc": "bachelor of science", "bsc": "bachelor of science",
        "m sc": "master of science", "msc": "master of science",
        "b a": "bachelor of arts", "m a": "master of arts",
        "b eng": "bachelor of engineering", "m eng": "master of engineering",
        "ll b": "bachelor of laws", "ll m": "master of laws",
        "dipl kfm": "diplom kaufmann", "dipl kffr": "diplom kauffrau",
        "dipl ing": "diplom ingenieur", "dipl inf": "diplom informatiker",
        "dipl psych": "diplom psychologe", "dipl volksw": "diplom volkswirt",
        "dipl betriebsw": "diplom betriebswirt",
    }.items()
)
#: A leading phrase that only frames the qualification ("Ausbildung zur
#: Industriekauffrau" ↔ "Industriekauffrau (IHK)", "Promotion (Dr. rer. nat.)").
_ENTRY_WRAPPER = re.compile(r"^(?:ausbildung (?:zum|zur|als)|abschluss (?:als|zum|zur)|promotion|studium) (?=\S)")
#: A comma tail naming the school or a year is no part of the credential's name.
_ENTRY_TAIL = re.compile(r",\s*(?=[^,]*(?:universit|hochschule|college|school|akademie|\b(?:19|20)\d\d\b))[^,]*$", re.I)


#: 790:69 (Hazelnut build 4) — a degree's FIELD OF STUDY in English ↔ its German
#: name, folded onto the German form on BOTH sides, inside a degree name only
#: ("B.Sc. Computer Science" ↔ vault "Bachelor of Science Informatik", fo report
#: §2: 2 of the 3 priced reds). A fixed table of the same field in two languages,
#: never a family: "Informatik" and "Wirtschaftsinformatik" stay different fields.
_ENTRY_FIELDS: tuple[tuple[str, str], ...] = (
    ("business information systems", "wirtschaftsinformatik"),
    ("business informatics", "wirtschaftsinformatik"),
    ("information systems", "wirtschaftsinformatik"),
    ("computer science", "informatik"),
    ("business administration", "betriebswirtschaftslehre"),
    ("bwl", "betriebswirtschaftslehre"),
    ("economics", "volkswirtschaftslehre"),
    ("vwl", "volkswirtschaftslehre"),
    ("industrial engineering", "wirtschaftsingenieurwesen"),
    ("mechanical engineering", "maschinenbau"),
    ("electrical engineering", "elektrotechnik"),
    ("civil engineering", "bauingenieurwesen"),
    ("mathematics", "mathematik"),
    ("physics", "physik"),
    ("chemistry", "chemie"),
    ("psychology", "psychologie"),
)
_ENTRY_FIELD_RES = tuple((re.compile(rf"(?<!\S){re.escape(en)}(?!\S)"), de) for en, de in _ENTRY_FIELDS)
_ENTRY_DEGREE_LEAD = re.compile(r"^(bachelor|master|diplom|doctor) (of (science|arts|engineering|laws|business administration) )?")


def _field_of_study(key: str) -> str:
    """790:69 — inside a degree name, the field in its German form and the
    linking "in" dropped ("bachelor of science in computer science" →
    "bachelor of science informatik"). Any other key is returned unchanged."""
    m = _ENTRY_DEGREE_LEAD.match(key)
    if not m:
        return key
    head, rest = key[: m.end()], key[m.end():]
    if rest.startswith("in "):
        rest = rest[3:]
    for rx, de in _ENTRY_FIELD_RES:
        rest = rx.sub(de, rest)
    return (head + rest).strip()


def _entry_key(text: str) -> str:
    s = fold(_ENTRY_STRIP.sub("", text or ""))
    s = _ENTRY_PUNCT.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _ENTRY_WRAPPER.sub("", s)
    s = _ENTRY_SYNONYMS.get(s, s)
    toks = s.split()
    for abbr, long in _ENTRY_PREFIXES:
        if tuple(toks[: len(abbr)]) == abbr:
            return _field_of_study(" ".join([long] + toks[len(abbr):]))
    return _field_of_study(s)


def entry_keys(name: str) -> frozenset[str]:
    """Folded identity forms of an entry name: as written, and without its
    parenthetical/bracketed part ("Industriemeister Metall (IHK)" ↔
    "Industriemeister Metall")."""
    name = _ENTRY_TAIL.sub("", name or "")
    keys = {_entry_key(name), _entry_key(_ENTRY_PAREN.sub(" ", name))}
    return frozenset(k for k in keys if k)


def _inner_keys(name: str) -> frozenset[str]:
    """The parenthesised part of a VAULT entry when it names the entry rather
    than qualifying it ("Certified Public Accountant (CPA)" → "cpa"). Vault side
    only: on the document side a bracket would let "Wirtschaftsprüfer (CPA)" ride
    on a vault "CPA" (main review of 03c6f117)."""
    out = set()
    for m in _ENTRY_PAREN.finditer(name or ""):
        inner = _entry_key(m.group(1) or m.group(2) or "")
        if len(inner) >= 2 and inner not in _ENTRY_QUALIFIERS:
            out.add(inner)
    return frozenset(out)


#: Entry-only family forms. "Dr. rer. pol." / "Dr.-Ing." name a doctorate as an
#: entry; in PROSE the same token mostly names another person ("mit Dr. med.
#: Weber"), so E-2's sentence detection deliberately does not read it.
_ENTRY_EXTRA: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("doctorate", re.compile(
        r"^\s*dr\.?\s*$|\bdr\.?\s?-?\s?(?:rer|phil|med|ing|jur|oec|habil|nat|pol|sc)\b", re.I)),
)


def entry_families(name: str) -> frozenset[str]:
    """The credential families an entry name carries (frame-free: the entry is
    the ascription). Read only to decide the other-language case: a name miss
    with a known, language-neutral family ("CPA", "MBA", "Dr.") stays red, one
    without is left open. ``certified_x`` names a translatable phrase
    ("Zertifizierter Scrum Master" ↔ "Certified Scrum Master"), so it never
    counts here; the German chamber/trade families do (an "Industriekauffrau" an
    English vault never names is red — main review of 03c6f117)."""
    out: set[str] = {fam for fam, rx in _ENTRY_EXTRA if rx.search(name or "")}
    for fam, rx, _backing in _COMPILED:
        if fam in _ENTRY_TRANSLATABLE:
            continue
        if rx.search(name or ""):
            out.add(fam)
    return frozenset(out)


def _shortening(doc_key: str, vault_key: str) -> bool:
    """The document's key keeps ≥ 2 of a vault key's tokens, in order, and adds
    none ("Scrum Master" of "Professional Scrum Master I", "ITIL Foundation" of
    "ITIL 4 Foundation"). Dropping words weakens a claim; adding one makes a new
    claim, so one direction only: "ISO 9001 Lead Auditor" over "ISO 9001", or
    "Professional Scrum Master II" over "… Master I", stays unbacked. Compound
    spelling is folded first ("ScrumMaster" ↔ "Scrum Master")."""
    if doc_key.replace(" ", "") == vault_key.replace(" ", ""):
        return True
    d, v = doc_key.split(), vault_key.split()
    if len(d) < 2:
        return False
    it = iter(v)
    return all(tok in it for tok in d)


@dataclass(frozen=True)
class EntryBacking:
    #: ``"name"`` (a vault entry names it) or ``None`` (nothing backs it).
    how: str | None
    #: The backing vault entry's path, e.g. ``certifications[2].name``.
    path: str | None = None
    #: The families the document entry carries (empty = none known).
    families: frozenset[str] = frozenset()


def entry_backing(name: str, vault_entries: tuple[tuple[str, str], ...]) -> EntryBacking:
    """Whether (and by which entry) the claimable vault's entry names back a
    document entry."""
    doc_keys = entry_keys(name)
    fams = entry_families(name)
    for path, vname in vault_entries:
        vkeys = entry_keys(vname) | _inner_keys(vname)
        if doc_keys & vkeys or any(_shortening(d, v) for d in doc_keys for v in vkeys):
            return EntryBacking("name", path, fams)
    return EntryBacking(None, None, fams)
