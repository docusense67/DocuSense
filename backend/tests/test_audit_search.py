"""Audit trail search and the per-document timeline (Section 5H, 17)."""

from datetime import UTC, datetime, timedelta

from app.models import AuditLog
from app.models.enums import Role
from app.services import audit


def test_search_filters_and_pages(client, make_user, login, db):
    admin = make_user(Role.ADMIN)
    audit.record(db, "grade_change_approved", user_id=admin.id, entity_type="grade",
                 entity_id=7, new_value={"grade": "1.25", "reason": "Encoding error IT101"})
    audit.record(db, "document_released", user_id=admin.id, entity_type="generated_document",
                 entity_id=3, new_value={"document_number": "TOR-2026-000003"})
    db.add(AuditLog(action="login_failed", timestamp=datetime.now(UTC) - timedelta(days=40)))
    db.commit()
    login(admin.email)

    def search(**params):
        res = client.get("/api/audit-logs", params=params)
        assert res.status_code == 200
        return res.json()

    assert search(q="it101")["items"][0]["action"] == "grade_change_approved"
    assert search(q="TOR-2026-000003")["items"][0]["entity_id"] == "3"
    assert search(entity_type="generated_document", entity_id="3")["total"] == 1
    assert search(q="admin@school")["total"] >= 2
    recent = search(date_from=(datetime.now(UTC) - timedelta(days=7)).date().isoformat())
    assert "login_failed" not in {i["action"] for i in recent["items"]}
    page = search(limit=1)
    assert page["total"] >= 4 and len(page["items"]) == 1  # newest first, one per page


def test_students_and_teachers_cannot_read_the_audit_log(client, make_user, login):
    for role in (Role.STUDENT, Role.TEACHER):
        user = make_user(role)
        login(user.email)
        assert client.get("/api/audit-logs").status_code == 403
        client.post("/api/auth/logout")
