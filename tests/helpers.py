"""Test utilities, mock database dependencies, and auth helpers."""

import os
import secrets
import sys

# Random passwords for the starting accounts, made fresh on every run: no password is written here.
PASSWORDS = {name: secrets.token_urlsafe(12) for name in ("admin", "barista", "booth_lead")}
WRONG_PASSWORD = secrets.token_urlsafe(12)
os.environ["ADMIN_PASSWORD"] = PASSWORDS["admin"]
os.environ["BARISTA_PASSWORD"] = PASSWORDS["barista"]
os.environ["BOOTH_LEAD_PASSWORD"] = PASSWORDS["booth_lead"]

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.auth import hash_password
from app import database
from app.database import get_session
from app.main import app, seed_default_data
from app.models import User

# Always use a disposable database: setUp drops and recreates its tables.
# SQLite by default; TEST_DATABASE_URL enables isolated PostgreSQL verification.
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite:///test_beansbeats.db")
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False} if TEST_DB_URL.startswith("sqlite") else {})


def override_get_session():
    with Session(test_engine) as session:
        yield session


app.dependency_overrides[get_session] = override_get_session
database.new_session = lambda: Session(test_engine)


def setup_test_db():
    SQLModel.metadata.drop_all(test_engine)
    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        seed_default_data(session)


def get_test_client() -> TestClient:
    from app.routers.auth import reset_login_limits
    from app.routers.orders import reset_guest_cache
    reset_login_limits()
    reset_guest_cache()
    setup_test_db()
    return TestClient(app)


def get_token(client: TestClient, username: str = "barista", password: str | None = None) -> str:
    password = password or PASSWORDS[username]
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}: {res.text}"
    return res.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
