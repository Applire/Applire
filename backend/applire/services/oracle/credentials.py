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
    ("fachwirt", r"\b\w*fachwirt" + _G + r"\b", ("fachwirt",)),
    ("betriebswirt_ihk", r"\b(?:staatlich geprüfte?r?|geprüfte?r?)\s+betriebswirt" + _G + r"\b", ("betriebswirt",)),
    ("techniker_sg", r"\bstaatlich geprüfte?r?\s+techniker" + _G + r"\b", ("techniker",)),
    ("fasi", r"\bfachkraft für arbeitssicherheit\b|\bsicherheitsfachkraft\b|\bsifa\b",
     ("fachkraft für arbeitssicherheit", "sicherheitsfachkraft", "sifa")),
    ("ada", r"\bausbildereignung\w*|\bada[- ]schein\b", ("ausbildereignung", "ada schein", "ada")),
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
    "registered_nurse", "chartered_acc",
})
_FRAME = re.compile(
    r"(?:\b(?:ich bin|bin ich|bin|als|zum|zur|ausgebildete?r?|gelernte?r?|geprüfte?r?|examinierte?r?|"
    r"staatlich (?:geprüfte?r?|anerkannte?r?)|i am an?|i'm an?|as an?|qualified|work(?:ed|ing)? as an?)"
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
    for name, rx, _backing in _COMPILED:
        if name in seen:
            continue
        for m in rx.finditer(text):
            if name in _TITLE_FAMILIES and not _ascribed(text, m.start(), kind):
                continue
            out.append(CredentialMention(name, m.group(0).strip()))
            seen.add(name)
            break
    return out


def is_backed(mention: CredentialMention, vault_folded: str) -> bool:
    """Lenient: any form of the family anywhere in the folded claimable vault."""
    for name, _rx, forms in _COMPILED:
        if name != mention.family:
            continue
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
