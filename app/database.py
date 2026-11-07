"""Database engine, table creation, and per-request session dependency."""

import os
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session, SQLModel, create_engine

# PostgreSQL when deployed (e.g., in Docker Compose); SQLite for local development and tests
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///beansbeats.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def new_session() -> Session:
    """A session outside FastAPI's dependencies; tests point it at their DB."""
    return Session(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

