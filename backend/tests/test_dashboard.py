"""Epic 7 backend: registrar dashboard aggregates, student eligibility, validation stats/export."""

from app.timeutil import PH_TZ, utcnow
from tests.test_scanning import env, extracted_from, isolated, submit  # noqa: F401

DOMAIN = "school.edu.ph"


def test_registrar_dashboard(env):
    login = env["login"]
    login("ana.garcia").post("/api/requests", json={"document_type": "TOR",
                                                     "purpose": "Transfer"})  # rejected: INC
    login("juan.delacruz").post("/api/requests", json={"document_type": "COE",
                                                        "purpose": "Scholarship"})
    body = login("registrar").get("/api/dashboard/registrar").json()

    today = body["requests"]["today"]
    assert today["total"] == 3  # Maria's TOR (released in fixture), Ana, Juan
    assert today["rejected"] == 1 and today["pending"] == 1 and today["released"] == 1
    assert today["approved"] == 0
    assert [q["document_type"] for q in body["approval_queue"]] == ["COE"]  # needs approval
    assert body["verification_queue"] == []

    month = utcnow().strftime("%Y-%m")
    this_month = next(m for m in body["documents_by_month"] if m["month"] == month)
    assert this_month == {"month": month, "TOR": 1, "COE": 0}  # COE not generated: pending

    # Approving Juan's COE generates it and moves it to the verification queue.
    registrar = login("registrar")
    registrar.post(f"/api/requests/{body['approval_queue'][0]['id']}/approve")
    body = registrar.get("/api/dashboard/registrar").json()
    assert body["requests"]["today"]["approved"] == 1 and body["approval_queue"] == []
    assert [q["document_type"] for q in body["verification_queue"]] == ["COE"]
    assert len(body["documents_by_month"]) == 6

    ana = next(h for h in body["data_health"] if h["student_number"] == "2025-00003")
    assert "1 INC grade(s)" in ana["issues"]
    assert body["recent_activity"][0]["action"]


def test_dashboard_date_range(env):
    submit(env, extracted_from(env["doc"]))
    registrar = env["login"]("registrar")
    today = utcnow().astimezone(PH_TZ).date()
    body = registrar.get(f"/api/dashboard/registrar?date_from={today}&date_to={today}").json()
    assert body["requests"]["range"]["total"] == 1 and body["validation"]["total"] == 1
    old = registrar.get("/api/dashboard/registrar?date_from=2020-01-01&date_to=2020-12-31").json()
    assert old["requests"]["range"]["total"] == 0 and old["validation"]["total"] == 0
    assert "range" not in registrar.get("/api/dashboard/registrar").json()["requests"]


def test_dashboard_access(env):
    assert env["login"]("admin").get("/api/dashboard/registrar").status_code == 200
    assert env["login"]("maria.santos").get("/api/dashboard/registrar").status_code == 403


def test_student_eligibility(env):
    assert env["login"]("maria.santos").get("/api/requests/eligibility").json() == {
        "TOR": [], "COE": []}
    ana = env["login"]("ana.garcia").get("/api/requests/eligibility").json()
    assert any("Incomplete grade" in r for r in ana["TOR"]) and ana["COE"] == []


def test_validation_stats_and_exports(env):
    payload = extracted_from(env["doc"])
    payload["table"]["rows"][0]["grade"] = "1.00"
    submit(env, extracted_from(env["doc"]))
    submit(env, payload)
    client = env["client"]

    stats = client.get("/api/validations/stats").json()
    assert stats["results"] == {"VALID": 1, "INVALID": 1, "NEEDS_REVIEW": 0}
    assert stats["failed_fields"] == [{"field": "grade", "count": 1}]

    csv = client.get(f"/api/validations/export?session_id={env['session']['session_id']}")
    assert csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.strip().splitlines()
    assert lines[0].startswith("position,scanned_at") and len(lines) == 3
    assert client.get("/api/validations/export?format=xml").status_code == 422


def test_validation_pdf_export(env, monkeypatch):
    from app.documents.pdf import RenderedPdf
    monkeypatch.setattr("app.api.scanning.render_pdf",
                        lambda html, a, b: RenderedPdf(b"%PDF-1.7 report", 1))
    submit(env, extracted_from(env["doc"]))
    res = env["client"].get("/api/validations/export?format=pdf")
    assert res.status_code == 200 and res.content.startswith(b"%PDF")
