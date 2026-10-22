import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import User
from app.models.enums import Role
from app.security import hash_password

PASSWORD = "correct-horse-battery"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestSession()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    yield session
    app.dependency_overrides.clear()
    session.close()


@pytest.fixture
def client(db):
    # https base URL so the Secure session cookie is sent back.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def make_user(db):
    def _make(role: Role, email: str | None = None, *, must_change: bool = False, **kw) -> User:
        user = User(
            email=email or f"{role}@school.edu.ph",
            password_hash=hash_password(PASSWORD),
            role=role,
            must_change_password=must_change,
            **kw,
        )
        db.add(user)
        db.commit()
        return user

    return _make


@pytest.fixture
def login(client):
    def _login(email: str, password: str = PASSWORD):
        return client.post("/api/auth/login", json={"email": email, "password": password})

    return _login
