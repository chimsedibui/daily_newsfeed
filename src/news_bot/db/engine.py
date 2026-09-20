from __future__ import annotations

import functools
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import REPO_ROOT, get_settings


@functools.lru_cache(maxsize=1)
def get_engine() -> Engine:
    s = get_settings()
    return create_engine(
        s.postgres_dsn,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        future=True,
        connect_args={"options": f"-csearch_path={s.db_schema},public"},
    )


@functools.lru_cache(maxsize=1)
def _session_factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), future=True, expire_on_commit=False)


@contextmanager
def session_scope() -> Session:
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_schema(sql_file: Path | None = None) -> None:
    """Ap dung sql/001_init.sql. Idempotent - goi o task preflight cua Airflow."""
    sql_file = sql_file or REPO_ROOT / "sql" / "001_init.sql"
    ddl = sql_file.read_text(encoding="utf-8")
    with get_engine().begin() as conn:
        conn.execute(text(ddl))
