"""Database engine, table creation, and per-request session dependency."""

import os
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session, SQLModel, create_engine

# PostgreSQL when deployed (e.g., in Docker Compose); SQLite for local development and tests
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///beansbeats.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
# PostgreSQL: up to 40 connections;
# each is checked before reuse, so a dropped connection is replaced instead of failing a request.
pool = {} if DATABASE_URL.startswith("sqlite") else {"pool_size": 20, "max_overflow": 20, "pool_timeout": 15, "pool_pre_ping": True}
engine = create_engine(DATABASE_URL, connect_args=connect_args, **pool)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def new_session() -> Session:
    """A session outside FastAPI's dependencies; tests point it at their DB."""
    return Session(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

