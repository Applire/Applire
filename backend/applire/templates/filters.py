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

from jinja2 import Environment, FileSystemLoader, select_autoescape

__all__ = ["month_year", "budget_display", "register_filters", "build_template_env"]

# "2017-04", "2017-04-01", "2017/04" — the shapes the extractor and the reconciler
# actually produce. Anything else is passed through untouched (see month_year).
_ISO_MONTH_RE = re.compile(r"^\s*(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?\s*$")
_YEAR_ONLY_RE = re.compile(r"^\s*(\d{4})\s*$")

# A value that is ENTIRELY a number — digits plus the separators a human or a
# reconciler might already have put in (thousands ``.``/``,``/space, at most
# one decimal ``.``/``,``) — vs. one that carries wording (a magnitude word,
# a currency symbol, a tilde, ...). Only the former is ours to reformat.
_BARE_NUMBER_RE = re.compile(r"^\d[\d.,\s]*$")


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
    """Render a stored ``budget_managed`` value for display (adversarial pass,
    2026-07-30, finding 1).

    ``GET /api/cv/{id}/html`` shipped ``Budget: 6000000`` as document
    furniture two lines above the writer's own prose quoting the SAME figure
    formatted ("ca. 6 Mio. € pro Jahr") — the vault value (a bare digit
    string, ``_apply_role_facts``'s correct, unconditional passthrough of
    what the reconciler stored, ADR-062 clause 1) is a fine queryable
    projection; nothing formatted it for a human reader.

    A value that is **entirely a number** (optionally already carrying
    thousands separators or a decimal comma/point — the reconciler's own
    ``str(int)``/``str(float)`` coercion, see ``test_reconcile_apply.py``'s
    "Field-type coercion" section, is the shape actually seen in practice) is
    grouped for the document's language: ``.`` thousands-separated for
    German, ``,`` for everything else. A value that already carries human
    wording — a magnitude word ("Mio."), a currency symbol/code ("€", "EUR"),
    a tilde ("~6m") — is **passed through untouched**: the writer already
    said what it meant, and reformatting prose is not this filter's job.

    Chosen over a "6 Mio." magnitude form: grouping is always exact for any
    figure (a magnitude form needs a rounding decision the moment the number
    isn't a clean multiple of a million) and needs no per-language
    magnitude-word table — the same reasoning :func:`month_year` gives for
    not localising month names.

    Never invents a currency: the output is digits and separators only, even
    when the stored value carries none — inventing "€6.000.000" from a bare
    "6000000" would assert a fact (the currency) the vault never stated.

    >>> budget_display("6000000", "de")
    '6.000.000'
    >>> budget_display("6000000", "en")
    '6,000,000'
    >>> budget_display("ca. 6 Mio. EUR", "de")
    'ca. 6 Mio. EUR'
    >>> budget_display(None)
    ''
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""

    if not _BARE_NUMBER_RE.match(text):
        # Already worded — a magnitude word, a currency symbol, a tilde, ...
        # Never reformatted, never stripped.
        return text

    segments = [s for s in re.split(r"[.,\s]", text) if s]
    if not segments:
        # All separators, no actual digits (malformed) — fail open, same
        # policy as month_year's unrecognised-value branch.
        return text

    # A trailing 1- or 2-digit segment is a decimal fraction; anything else —
    # including a trailing 3-digit segment — is a thousands group, because an
    # already-grouped whole number (the overwhelmingly common shape here) is
    # far more likely than a budget figure carrying cents.
    if len(segments) > 1 and len(segments[-1]) in (1, 2):
        integer_part, fraction = "".join(segments[:-1]), segments[-1]
    else:
        integer_part, fraction = "".join(segments), ""
    if not integer_part:
        return text

    thousands_sep = "." if lang == "de" else ","
    grouped = f"{int(integer_part):,}".replace(",", thousands_sep)
    if fraction:
        decimal_sep = "," if lang == "de" else "."
        grouped = f"{grouped}{decimal_sep}{fraction}"
    return grouped


def register_filters(env) -> None:
    """Install every shared template filter on a Jinja environment.

    Prefer :func:`build_template_env`; this exists for an environment that already
    has to be constructed some other way.
    """
    env.filters["month_year"] = month_year
    env.filters["budget_display"] = budget_display


def build_template_env(templates_dir) -> Environment:
    """The one way to construct a Jinja environment for Applire's templates.

    Production render sites and test harnesses alike — see the module docstring.
    A test asserts no ``Environment(`` is constructed anywhere else.
    """
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    register_filters(env)
    return env
