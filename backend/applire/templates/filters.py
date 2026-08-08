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

from applire.utils.budget_unit import budget_needs_unit

__all__ = ["month_year", "budget_display", "register_filters", "build_template_env"]

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
