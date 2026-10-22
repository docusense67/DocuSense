"""Notifications follow real workflow events and preserve student isolation."""

from tests.test_requests import as_user, isolated, request_doc, seeded  # noqa: F401


def test_request_and_approval_notifications(as_user):
    client = as_user("maria.santos")
    request = request_doc(client).json()
    assert request["status"] == "for_approval"
    client = as_user("registrar")
    response = client.get("/api/notifications")
    assert response.status_code == 200
    notifications = response.json()
    assert {n["title"] for n in notifications} >= {
        "Request filed", "Request waiting for approval",
    }
    assert all(n["href"] == f"/requests/{request['id']}" for n in notifications)
    assert all(request["reference_no"] in n["message"] for n in notifications)
    assert [n["id"] for n in notifications] == sorted(
        [n["id"] for n in notifications], reverse=True,
    )
    assert client.post(f"/api/requests/{request['id']}/approve").status_code == 200
    own = as_user("maria.santos").get("/api/notifications").json()
    assert {n["title"] for n in own} >= {"Request approved", "Document ready to download"}
    assert all(n["href"] == "/" for n in own)
    assert as_user("juan.delacruz").get("/api/notifications").json() == []
    assert as_user("teacher").get("/api/notifications").json() == []
    admin = as_user("admin").get("/api/notifications").json()
    assert admin and all(n["href"] == "/overview" for n in admin)


def test_notifications_require_login(client):
    assert client.get("/api/notifications").status_code == 401
