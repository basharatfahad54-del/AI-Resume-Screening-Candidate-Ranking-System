"""Generic offset pagination helper."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Generic, Sequence, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: Sequence[T]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return ceil(self.total / self.page_size)

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def has_prev(self) -> bool:
        return self.page > 1


async def paginate(
    session: AsyncSession,
    stmt,
    *,
    page: int = 1,
    page_size: int = 20,
) -> Page:
    """Execute ``stmt`` for one page and return a :class:`Page`.

    ``stmt`` must be a ``select()`` returning ORM entities; the count query
    reuses the same ``whereclause``/``order_by`` so filters stay consistent.
    """
    page = max(1, page)
    page_size = max(1, page_size)

    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int((await session.execute(count_stmt)).scalar_one() or 0)

    rows = (
        await session.execute(stmt.limit(page_size).offset((page - 1) * page_size))
    ).scalars().all()
    return Page(items=rows, total=total, page=page, page_size=page_size)
