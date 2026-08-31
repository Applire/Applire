# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""Shared render-time Jinja filters for the CV and cover-letter templates (#307).

**Build every environment with :func:`build_template_env`.** The tree had SEVEN
independent Jinja environments when this module was written — ``cv.py``,
``cover_letter.py``, ``thumbnails.py``, and four test harnesses (the section editor
and the ``tests/ats`` roundtrip borrow ``cv.py``'s) — each constructed by hand with
the same two arguments. Adding the first shared filter broke all four test harnesses
at once, precisely the render-site drift ``CLAUDE.md`` warns about, and patching each
site would have left the eighth to be discovered the same way. A single factory means
there is one way to get an environment and it is always correctly configured.
"""
import re
import unicodedata

from jinja2 import Environment, FileSystemLoader

from applire.utils.budget_unit import budget_needs_unit

__all__ = [
    "month_year",
    "budget_display",
    "education_title",
    "register_filters",
    "build_template_env",
]

# "2017-04", "2017-04-01", "2017/04" — the shapes the extractor and the reconciler
# actually produce. Anything else is passed through untouched (see month_year).
_ISO_MONTH_RE = re.compile(r"^\s*(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?\s*$")
_YEAR_ONLY_RE = re.compile(r"^\s*(\d{4})\s*$")


def month_year(value: object) -> str:
    """Render a stored partial date in MM/YYYY, the DACH CV convention (#307).

    Charter run #7 shipped ``2017-04 – heute`` in a German CV's date column; the
    blind HR screener flagged it unprompted as "unüblich in einer DACH-Bewerbung".
    The stored value is unchanged — this is presentation only.

    MM/YYYY is used for **every** output language, not only German. It is the DACH
    standard, it is unremarkable on an English CV, and it needs no localised month
    names — which would otherwise add a per-language table (and a new way for a
    language to be half-supported) to fix a formatting bug.

    Never raises and never drops information: a value that is not a recognised
    partial date is returned as-is, because the alternative is a blank date column
    on a real candidate's CV.

    >>> month_year("2017-04")
    '04/2017'
    >>> month_year("2022-01-01")
    '01/2022'
    >>> month_year("2016")
    '2016'
    >>> month_year(None)
    ''
    """
    if value is None:
        return ""
    text = str(value)

    match = _ISO_MONTH_RE.match(text)
    if match:
        year, month = match.group(1), int(match.group(2))
        # A 13th month means the value is not what we think it is — pass it through
        # rather than rendering "13/2017" with false confidence.
        if 1 <= month <= 12:
            return f"{month:02d}/{year}"
        return text.strip()

    if _YEAR_ONLY_RE.match(text):
        # A year alone is already conventional on both a CV and a certificate.
        return text.strip()

    return text.strip()


def budget_display(value: object, lang: str = "de") -> str:
    """Render a stored ``budget_managed`` value as document furniture — or
    render NOTHING when it states no unit (#382, PO decision 2026-08-08).

    ``GET /api/cv/{id}/html`` shipped ``Budget: 6000000`` as document furniture
    two lines above the writer's own prose quoting the SAME figure formatted
    ("ca. 6 Mio. € pro Jahr"). The vault value is a bare digit string with no
    unit — the reconciler's own ``str(int)``/``str(float)`` coercion (see
    ``test_reconcile_apply.py``'s "Field-type coercion" section), and the shape
    actually seen in practice.

    The first fix (2026-07-30, finding 1) grouped it — ``6.000.000`` — which
    made the number legible without making it *meaningful*: six million of what?
    **Option A supersedes it.** A budget wording that carries no unit is omitted
    from the delivered document entirely, because the three alternatives were
    each rejected on the record:

    * rendering the bare magnitude — ambiguous, and furniture reads as
      authoritative structured data rather than as prose a reader discounts;
    * grouping it — the same ambiguity, better dressed;
    * supplying a unit — fabrication. ``"6000000"`` never said €.

    The empty string is what makes the omission clean at the call site: every
    template guards the *rendered* value, so the label, the value and the
    surrounding separator disappear together instead of leaving ``Budget: ``.

    **The value is not deleted, and this filter is not the only guard.** The
    vault keeps it (it is real testimony) and ``services.cv._apply_role_facts``
    already drops it before it reaches a freshly tailored CV. This filter is the
    fail-safe for the case that pass cannot cover: a CV persisted BEFORE this
    change, re-rendered straight from stored ``tailored_data``. The figure
    re-enters the line the moment a unit is confirmed — the Health hub and the
    master profile page both ask for one (``utils.budget_unit``).

    Wording the candidate wrote is otherwise passed through untouched, in any
    language: reformatting prose is not this filter's job — the same reasoning
    :func:`month_year` gives for not localising month names. ``lang`` is
    therefore unused today and kept in the signature deliberately, as the one
    place a future per-language budget convention would attach; every template
    already threads it in.

    >>> budget_display("ca. 6 Mio. EUR", "de")
    'ca. 6 Mio. EUR'
    >>> budget_display("6000000", "de")
    ''
    >>> budget_display("6.000.000", "en")
    ''
    >>> budget_display(None)
    ''
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if budget_needs_unit(text):
        return ""
    return text


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return set(_TOKEN_RE.findall(normalized))


def education_title(degree: object, field: object = "") -> str:
    """Render an education entry's title, omitting ``field`` when it is
    already a verbatim word-for-word part of ``degree`` (#548).

    Charter run 2026-08-14 delivered ``Industriemeister Metall, Metall`` in
    the German CV's education section (two models, both DE runs, build
    ``c8cb4fa9``) — a blind HR reviewer flagged it unprompted. Ground truth
    (``tests/files/panel_review_case/operations_marcus_de/cv_stefan_brandt.md``):
    the source states the qualification on ONE line, ``Industriemeister Metall
    (IHK), 2010`` — a German Meister title where the specialisation is fused
    into the title itself, unlike e.g. ``Bachelor of Science`` + ``Informatik``
    where ``field`` names something the degree string does not already say.
    The extraction prompt's schema described ``field`` only as "field of study
    or specialisation" with no guidance on this distinction (a prompt gap —
    see the sibling fix in ``prompts/cv_extraction.py`` and
    ``prompts/cv_extraction_segmented.py``); locally the default provider left
    ``field`` empty for this exact entry, but BYOI (ADR-054) means any
    self-hosted or cloud model can be plugged in, and the two models that
    produced the bug are real, supported configurations — a prompt rule
    cannot be the only defence.

    This filter is that defence, at the one join every one of the 7 CV
    templates previously duplicated inline (ADR-066). It normalises with
    unicode NFKC + casefold and checks EXACT token containment only — no
    fuzzy matching, no synonym dictionary (ADR-076 clause 4: paraphrase and
    semantic equivalence are judgements, never settled by string comparison;
    exact verbatim-token containment is the closed mechanical remainder).
    A legitimately distinct field (its tokens are not a subset of the
    degree's tokens) is never dropped.

    >>> education_title("Industriemeister Metall", "Metall")
    'Industriemeister Metall'
    >>> education_title("Bachelor of Science", "Informatik")
    'Bachelor of Science, Informatik'
    >>> education_title("Fachinformatiker Systemintegration", "Systemintegration")
    'Fachinformatiker Systemintegration'
    >>> education_title("Diplom-Ingenieur", None)
    'Diplom-Ingenieur'
    >>> education_title(None, "Metall")
    'Metall'
    """
    degree_text = str(degree).strip() if degree is not None else ""
    field_text = str(field).strip() if field is not None else ""

    if not field_text:
        return degree_text
    if not degree_text:
        return field_text

    field_tokens = _tokens(field_text)
    if field_tokens and field_tokens.issubset(_tokens(degree_text)):
        return degree_text

    return f"{degree_text}, {field_text}"


def register_filters(env) -> None:
    """Install every shared template filter on a Jinja environment.

    Prefer :func:`build_template_env`; this exists for an environment that already
    has to be constructed some other way.
    """
    env.filters["month_year"] = month_year
    env.filters["budget_display"] = budget_display
    env.filters["education_title"] = education_title


def build_template_env(templates_dir) -> Environment:
    """The one way to construct a Jinja environment for Applire's templates.

    Production render sites and test harnesses alike — see the module docstring.
    A test asserts no ``Environment(`` is constructed anywhere else.
    """
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        # #634: unconditional, NOT ``select_autoescape([...])``. That helper
        # decides per template *filename suffix*, and every Applire template is
        # named ``*.html.j2`` — so ``select_autoescape(["html"])`` never fired
        # on a single one of them, and a bullet reading "Koordination mit
        # <Projekt Phoenix>" shipped to the candidate's PDF with the bracketed
        # phrase silently deleted by the HTML parser. This directory holds only
        # HTML templates; anything else added here would need its own env.
        autoescape=True,
    )
    register_filters(env)
    return env
