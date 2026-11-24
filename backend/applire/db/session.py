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

from collections.abc import AsyncGenerator

from fastapi import Request

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from applire.config import settings

engine = create_async_engine(settings.database_url, echo=False)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db(request: Request = None) -> AsyncGenerator[AsyncSession, None]:  # type: ignore[assignment]
    """The request's session. Its connection goes back to the pool as soon as
    the endpoint has answered — before the response's background tasks run.

    FastAPI closes a request-scoped ``yield`` dependency only after the
    response has been sent, and the response's background tasks run inside
    that send: a request that read after its last commit held its connection
    "idle in transaction" for the whole background task (a CV render: minutes),
    and a burst of generations starved the pool (adv/plans re-probe N3). The
    endpoint-scoped exit stack closes the session after the endpoint and its
    response serialisation; a closed session stays usable (it would begin a
    new transaction), and the request scope closes it once more at the end.
    """
    async with AsyncSessionLocal() as session:
        stack = request.scope.get("fastapi_function_astack") if request is not None else None
        if stack is not None:
            stack.push_async_callback(session.close)
        yield session


# ADR-092 cl. 8a — the statement guard lives on the application engine only
# (never the Engine class: alembic's own engine stays unguarded). On since W3
# (``applire.ownership.GUARD_ENABLED`` defaults to True, MD-24).
from applire.ownership import install_guard as _install_guard  # noqa: E402
from applire.ownership import install_orm_hooks as _install_orm_hooks  # noqa: E402

_install_guard(engine)
# ADR-092 cl. 1 + cl. 8b: owner fill / chain-owner check on every owned model's
# INSERT, and the (uncredited) loader criteria while the guard is on.
_install_orm_hooks(Base)
