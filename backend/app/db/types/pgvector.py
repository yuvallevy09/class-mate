from __future__ import annotations

import sqlalchemy as sa


class VectorUDT(sa.types.UserDefinedType):
    """DDL/view type for pgvector (used only for casting/bind expressions)."""

    cache_ok = True

    def __init__(self, dims: int) -> None:
        self.dims = int(dims)

    def get_col_spec(self, **kw) -> str:  # noqa: D401
        return f"vector({self.dims})"


class Vector(sa.types.TypeDecorator):
    """
    Runtime-safe pgvector binding for asyncpg:
    - Store vectors as text literals like: '[0.1,0.2,...]'
    - CAST the bind parameter to vector(d) in SQL so Postgres can parse it

    Note: DDL is managed by Alembic migrations (column is already vector(d)).
    """

    impl = sa.Text
    cache_ok = True

    def __init__(self, dims: int) -> None:
        super().__init__()
        self.dims = int(dims)

    def load_dialect_impl(self, dialect):  # noqa: ANN001
        return dialect.type_descriptor(sa.Text())

    def bind_expression(self, bindvalue):  # noqa: ANN001
        return sa.cast(bindvalue, VectorUDT(self.dims))


