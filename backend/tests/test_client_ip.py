"""Client address for the audit log behind proxies (ADGVS_TRUSTED_PROXY_HOPS)."""

import pytest
from sqlalchemy import select
from starlette.requests import Request

from app.api.deps import client_ip
from app.config import get_settings
from app.models import AuditLog
from app.models.enums import Role


def request(*forwarded: str, peer: str = "10.0.0.9") -> Request:
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers,
                    "client": (peer, 1234)})


@pytest.fixture
def hops(monkeypatch):
    def _set(n: int):
        monkeypatch.setattr(get_settings(), "trusted_proxy_hops", n)
    return _set


def test_default_never_reads_forwarded_headers(hops):
    hops(0)
    assert client_ip(request("6.6.6.6")) == "10.0.0.9"
    assert client_ip(request("6.6.6.6, 7.7.7.7")) == "10.0.0.9"
    assert client_ip(request()) == "10.0.0.9"


@pytest.mark.parametrize(("n", "forwarded", "expected"), [
    (1, ["203.0.113.7"], "203.0.113.7"),
    (1, ["6.6.6.6, 203.0.113.7"], "203.0.113.7"),  # client-written entry ignored
    # Render behind Vercel: client, then the Vercel hop appended by Render's proxy
    (2, ["203.0.113.7, 76.76.21.9"], "203.0.113.7"),
    (2, ["6.6.6.6, 1.2.3.4, 203.0.113.7, 76.76.21.9"], "203.0.113.7"),  # spoof attempt
    (2, ["6.6.6.6", "203.0.113.7, 76.76.21.9"], "203.0.113.7"),  # repeated header = one list
    (2, ["2001:db8::1 , 76.76.21.9"], "2001:db8::1"),
    (2, ["203.0.113.7"], "203.0.113.7"),  # fewer entries than hops: leftmost
])
def test_trusted_hops_pick_the_address_the_first_trusted_proxy_saw(hops, n, forwarded, expected):
    hops(n)
    assert client_ip(request(*forwarded)) == expected


@pytest.mark.parametrize("garbage", ["unknown, 76.76.21.9", "<script>, 76.76.21.9",
                                     "1.2.3.4.5, 76.76.21.9"])
def test_garbage_never_reaches_the_audit_log(hops, garbage):
    hops(2)
    assert client_ip(request(garbage)) == "10.0.0.9"


def test_no_header_falls_back_to_the_peer(hops):
    hops(2)
    assert client_ip(request()) == "10.0.0.9"


def test_login_audit_records_the_forwarded_client(hops, client, db, make_user, login):
    make_user(Role.STUDENT, "ana@school.edu.ph")
    headers = {"X-Forwarded-For": "6.6.6.6, 203.0.113.7, 76.76.21.9"}

    hops(0)
    client.post("/api/auth/login", json={"email": "ana@school.edu.ph", "password": "wrong-pass1"},
                headers=headers)
    hops(2)
    client.post("/api/auth/login", json={"email": "ana@school.edu.ph", "password": "wrong-pass1"},
                headers=headers)
    ips = [log.ip_address for log in db.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert ips == ["testclient", "203.0.113.7"]
