"""Shared async HTTP test setup for station/mechanism tests (no pytest fixtures needed).

ADGVS_INLINE_TEST_THREADS=1 is a sandbox-only workaround for blocked event-loop wakeup
sockets. Production concurrency is unchanged. Normal environments use AnyIO threads.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base, get_db
from app.main import app
from app.models import ScanStation, User, UserSession
from app.models.enums import Role
from app.security import hash_token
from app.timeutil import utcnow


async def inline_thread(func, *args, **kwargs):
    return func(*args)


class StationTestBase(unittest.IsolatedAsyncioTestCase):
    """Users: staff (registrar), admin, student, teacher; one scan station."""

    async def asyncSetUp(self):
        self.inline = patch('anyio.to_thread.run_sync', inline_thread) if os.getenv(
            'ADGVS_INLINE_TEST_THREADS') else None
        if self.inline:
            self.inline.start()
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False, expire_on_commit=False)

        def override_db():
            yield self.db
        app.dependency_overrides[get_db] = override_db
        self.storage = tempfile.TemporaryDirectory()
        self.old_storage = get_settings().storage_dir
        get_settings().storage_dir = self.storage.name
        self.users = {}
        for name, role in [('staff', Role.REGISTRAR), ('admin', Role.ADMIN),
                           ('student', Role.STUDENT), ('teacher', Role.TEACHER)]:
            user = User(email=f'{name}@school.edu.ph', password_hash='unused', role=role,
                        must_change_password=False)
            self.db.add(user)
            self.db.flush()
            self.db.add(UserSession(user_id=user.id,
                                    token_hash=hash_token(f'test-session-{name}'),
                                    created_at=utcnow(), last_seen_at=utcnow()))
            self.users[name] = user
        self.station = ScanStation(name='Test station', api_key_hash=hash_token('station-key'))
        self.db.add(self.station)
        self.db.commit()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url='https://testserver')

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()
        get_settings().storage_dir = self.old_storage
        self.storage.cleanup()
        if self.inline:
            self.inline.stop()

    def login(self, name):
        self.client.cookies.clear()
        self.client.cookies.set(get_settings().session_cookie_name, f'test-session-{name}')
