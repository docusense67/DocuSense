"""Epic 5 API: station lookup/submit, results, operator review (UC4-UC7)."""

import copy
import io
from datetime import timedelta

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select

from app.config import get_settings
from app.documents.pdf import RenderedPdf
from app.models import GeneratedDocument, ScanStation, User
from app.seed import DEMO_PASSWORD, seed
from app.timeutil import utcnow

DOMAIN = "school.edu.ph"


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("L", (40, 56), 255).save(out, "PNG")  # tiny: stored as uploaded
    return out.getvalue()


PNG = _png()


def fake_render(html, left, right):
    return RenderedPdf(content=b"%PDF-1.7", page_count=1)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path))
    monkeypatch.setattr("app.documents.service.render_pdf", fake_render)


@pytest.fixture
def env(client, db):
    """Seeded DB, Maria's TOR released, a registered station with an open single session."""
    seed(db)
    for user in db.scalars(select(User)):
        user.must_change_password = False
    db.commit()

    def login(handle):
        client.post("/api/auth/logout")
        assert client.post("/api/auth/login", json={"email": f"{handle}@{DOMAIN}",
                                                     "password": DEMO_PASSWORD}).status_code == 200
        return client

    req = login("maria.santos").post("/api/requests", json={"document_type": "TOR",
                                                             "purpose": "Employment"}).json()
    registrar = login("registrar")
    registrar.post(f"/api/requests/{req['id']}/approve")  # S-11: approve, then generate
    registrar.post(f"/api/requests/{req['id']}/verify")  # verify, auto-release
    station = login("admin").post("/api/stations", json={"name": "Registrar Station"}).json()
    session = login("registrar").post("/api/scan-sessions",
                                      json={"mode": "single", "station_id": station["id"]}).json()
    doc = db.scalar(select(GeneratedDocument))
    return {"client": client, "login": login, "key": station["api_key"],
            "station_id": station["id"], "session": session, "doc": doc}


def extracted_from(doc: GeneratedDocument, **changes) -> dict:
    """What a perfect scan of this document would extract."""
    snap = doc.data_snapshot
    fields = {k: {"value": snap[k], "confidence": 96.0}
              for k in ("document_number", "student_name", "student_number", "program",
                        "date_issued", "gwa")}
    rows = [{"term": f"{t['semester']}, AY {t['academic_year']}", **s,
             "confidence": {"subject_code": 95, "units": 95, "grade": 95}}
            for t in snap["terms"] for s in t["subjects"]]
    payload = {
        "position": 1, "identity_source": "qr", "qr_token": doc.qr_token,
        "document_number_read": doc.document_number,
        "pages": [{"sequence": 1, "qr_present": True,
                   "footer": {"document_number": doc.document_number, "page": 1,
                              "pages": 1, "ref": doc.data_hash[:12]}}],
        "fields": fields,
        "table": {"rows": rows, "term_gwas": [], "unparsed": []},
    }
    for key, value in changes.items():
        payload[key] = value
    return payload


def submit(env, payload, key=None, session_id=None, pages=None):
    import json
    return env["client"].post(
        f"/api/station/sessions/{session_id or env['session']['session_id']}/documents",
        headers={"X-Station-Key": key or env["key"]},
        data={"payload": json.dumps(payload)},
        files=[("pages", ("p1.png", p, "image/png")) for p in (pages or [PNG])],
    )


# ---- station side ---------------------------------------------------------------------------

def test_station_lookup_returns_zones_but_no_student_data(env):
    client, doc = env["client"], env["doc"]
    res = client.post("/api/station/lookup", headers={"X-Station-Key": env["key"]},
                      json={"qr_token": doc.qr_token})
    body = res.json()
    assert body["found"] and body["document_number"] == doc.document_number
    assert "grades_table" in body["field_zones"]["fields"]
    assert "MARIA" not in res.text and "snapshot" not in body
    unknown = client.post("/api/station/lookup", headers={"X-Station-Key": env["key"]},
                          json={"document_number": "TOR-2026-999999"})
    assert unknown.json() == {"found": False}
    assert client.post("/api/station/lookup", headers={"X-Station-Key": "nope"},
                       json={}).status_code == 401


def test_station_finds_its_open_session(env):
    res = env["client"].get("/api/station/session", headers={"X-Station-Key": env["key"]})
    # next_position: the position the camera station numbers its next sheet with.
    assert res.json() == {"session_id": env["session"]["session_id"], "mode": "single",
                          "next_position": 1}
    assert env["session"]["output_tray"] == "Single"


# ---- results (UC5-UC7) ----------------------------------------------------------------------

def test_uc5_genuine_scan_is_valid(env):
    res = submit(env, extracted_from(env["doc"]))
    assert res.status_code == 201
    assert res.json()["result"] == "VALID"
    detail = env["client"].get(f"/api/validations/{res.json()['scanned_document_id']}").json()
    assert detail["document"]["student_name"] == "MARIA R. SANTOS"
    assert detail["document"]["times_scanned"] == 1
    assert all(f["status"] == "match" for f in detail["fields"])
    assert "does not certify the physical integrity" in detail["note"]
    page = env["client"].get(f"/api/validations/{res.json()['scanned_document_id']}/pages/1")
    assert page.status_code == 200 and page.content == PNG


def test_uc6_altered_grade_is_invalid_with_field_shown(env):
    payload = extracted_from(env["doc"])
    payload["table"]["rows"][0]["grade"] = "1.00"
    res = submit(env, payload)
    assert res.json()["result"] == "INVALID"
    detail = env["client"].get(f"/api/validations/{res.json()['scanned_document_id']}").json()
    bad = [f for f in detail["fields"] if f["status"] == "mismatch"]
    assert len(bad) == 1 and bad[0]["printed"] == "1.00"


def test_fake_document_number_is_invalid(env):
    payload = extracted_from(env["doc"], qr_token=None, identity_source="ocr",
                             document_number_read="TOR-2026-424242")
    res = submit(env, payload)
    assert res.json()["result"] == "INVALID"
    assert "not issued by this institution" in res.json()["reasons"][0]


def test_forged_qr_with_a_real_document_number_is_not_valid(env):
    """A recreated document: real number and data, but a QR the school never issued."""
    payload = extracted_from(env["doc"], qr_token="Zm9yZ2VkLXRva2VuLW5vdC1vdXJz")
    res = submit(env, payload).json()
    assert res["result"] == "NEEDS_REVIEW"
    assert any("does not belong to any issued document" in r for r in res["reasons"])


def test_revoked_document_is_invalid(env):
    env["login"]("registrar").post(f"/api/documents/{env['doc'].id}/revoke",
                                   json={"reason": "Issued in error"})
    assert submit(env, extracted_from(env["doc"])).json()["result"] == "INVALID"


def test_uc7_bulk_session_batch_table(env):
    client = env["client"]
    bulk = client.post("/api/scan-sessions",
                       json={"mode": "bulk", "station_id": env["station_id"]}).json()
    assert bulk["output_tray"] == "Bulk"
    genuine = extracted_from(env["doc"])
    altered = copy.deepcopy(genuine)
    altered["table"]["rows"][1]["grade"] = "3.00"
    fake = extracted_from(env["doc"], qr_token=None, identity_source="ocr",
                          document_number_read="TOR-2026-000777")
    for position, payload in enumerate([genuine, altered, fake], start=1):
        payload["position"] = position
        assert submit(env, payload, session_id=bulk["session_id"]).status_code == 201

    batch = client.get(f"/api/scan-sessions/{bulk['session_id']}").json()
    assert batch["total"] == 3
    assert batch["totals"] == {"VALID": 1, "INVALID": 2, "NEEDS_REVIEW": 0}
    assert [d["result"] for d in batch["documents"]] == ["VALID", "INVALID", "INVALID"]
    # The previous single session was closed when the bulk one started.
    assert submit(env, genuine).status_code == 409


def test_review_needs_review_result(env):
    payload = extracted_from(env["doc"], qr_token=None, identity_source="ocr")  # no QR
    scanned_id = submit(env, payload).json()["scanned_document_id"]
    client = env["client"]
    assert client.post(f"/api/validations/{scanned_id}/review",
                       json={"final_result": "NEEDS_REVIEW", "remarks": "x" * 5}).status_code == 422
    res = client.post(f"/api/validations/{scanned_id}/review",
                      json={"final_result": "VALID", "remarks": "Checked original QR by eye"})
    assert res.json() == {"scanned_document_id": scanned_id, "result": "NEEDS_REVIEW",
                          "final_result": "VALID"}
    assert client.post(f"/api/validations/{scanned_id}/review",
                       json={"final_result": "INVALID", "remarks": "again"}).status_code == 409

    valid_id = submit(env, extracted_from(env["doc"])).json()["scanned_document_id"]
    assert client.post(f"/api/validations/{valid_id}/review",
                       json={"final_result": "INVALID", "remarks": "Override"}).status_code == 409


def test_bad_uploads_rejected(env):
    assert submit(env, extracted_from(env["doc"]), pages=[b"GIF89a..."]).status_code == 422
    # PNG magic but not an image: refused before anything is stored
    assert submit(env, extracted_from(env["doc"]),
                  pages=[b"\x89PNG\r\n\x1a\n" + b"\x00" * 64]).status_code == 422
    res = env["client"].post(
        f"/api/station/sessions/{env['session']['session_id']}/documents",
        headers={"X-Station-Key": env["key"]}, data={"payload": "{not json"},
        files=[("pages", ("p.png", PNG, "image/png"))])
    assert res.status_code == 422


def test_operator_permission_required(env, db):
    registrar = db.scalar(select(User).where(User.email == f"registrar@{DOMAIN}"))
    registrar.can_validate = False
    db.commit()
    client = env["client"]
    assert client.get("/api/validations").status_code == 403
    assert env["login"]("teacher").get("/api/scan-stations").status_code == 403


def test_scanner_status_reaches_dashboard(env):
    client = env["client"]
    res = client.post("/api/station/status", headers={"X-Station-Key": env["key"]},
                      json={"state": "jam", "detail": "Clear and re-feed from sheet 4"})
    assert res.status_code == 200
    stations = client.get("/api/scan-stations").json()
    assert stations[0]["status"] == "jam" and "sheet 4" in stations[0]["status_detail"]
    assert stations[0]["silent"] is False


def test_station_that_stopped_reporting_shows_silent_but_is_not_blocked(env, db):
    station = db.get(ScanStation, env["station_id"])
    station.last_status, station.last_seen_at = "ready", utcnow() - timedelta(minutes=5)
    db.commit()
    client = env["client"]
    assert client.get("/api/scan-stations").json()[0]["silent"] is True
    start = {"mode": "single", "station_id": env["station_id"]}
    assert client.post("/api/scan-sessions", json=start).status_code == 201


def test_uc4_offline_scanner_blocks_new_sessions(env):
    client, key = env["client"], env["key"]
    start = {"mode": "single", "station_id": env["station_id"]}
    client.post("/api/station/status", headers={"X-Station-Key": key},
                json={"state": "offline", "detail": "USB cable unplugged"})
    res = client.post("/api/scan-sessions", json=start)
    assert res.status_code == 409 and "USB cable unplugged" in res.json()["detail"]
    client.post("/api/station/status", headers={"X-Station-Key": key}, json={"state": "ready"})
    assert client.post("/api/scan-sessions", json=start).status_code == 201


def test_history_filters_by_result_type_and_date(env):
    client = env["client"]
    submit(env, extracted_from(env["doc"]))  # a TOR, VALID
    fake = extracted_from(env["doc"], qr_token=None, document_number_read="COE-2026-999999")
    submit(env, fake)  # matches nothing: INVALID, no document type

    assert len(client.get("/api/validations").json()) == 2
    assert [r["result"] for r in client.get("/api/validations?result=INVALID").json()] == [
        "INVALID"]
    assert len(client.get("/api/validations?document_type=TOR").json()) == 1
    assert client.get("/api/validations?document_type=COE").json() == []
    assert client.get("/api/validations?date_from=2000-01-01&date_to=2000-01-31").json() == []
    stats = client.get("/api/validations/stats?result=VALID").json()
    assert stats["total"] == 1
    export = client.get("/api/validations/export?format=csv&date_from=2026-01-01")
    assert 'filename="validation-2026-01-01-to-today.csv"' in export.headers[
        "content-disposition"]


# ---- cloud file storage ---------------------------------------------------------------------

@pytest.fixture
def bucket(monkeypatch):
    """ADGVS_STORAGE_BACKEND=supabase against an in-memory private bucket (no network)."""
    from pydantic import SecretStr

    from app import storage as storage_module
    from tests.test_storage import KEY, URL, FakeBucket, supabase

    fake = FakeBucket()
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "supabase")
    monkeypatch.setattr(settings, "supabase_url", URL)
    monkeypatch.setattr(settings, "supabase_service_key", SecretStr(KEY))
    monkeypatch.setattr(storage_module, "_supabase", lambda *args: supabase(fake))
    return fake


def a4_scan(width=2480) -> bytes:
    """A 300 dpi A4 page with some text, as the station uploads it (lossless PNG)."""
    page = Image.new("L", (width, round(width * 297 / 210)), 250)
    draw = ImageDraw.Draw(page)
    for row in range(40):
        draw.text((200, 300 + row * 70), f"IT{100 + row}  Introduction to Computing  3  1.25",
                  fill=20, font_size=40)
    out = io.BytesIO()
    page.save(out, "PNG")
    return out.getvalue()


def test_files_live_in_the_private_bucket_and_reach_browsers_only_through_the_api(bucket, env):
    doc = env["doc"]
    assert bucket.objects[doc.pdf_path] == (b"%PDF-1.7", "application/pdf")

    upload = a4_scan()
    res = submit(env, extracted_from(doc), pages=[upload])
    assert res.status_code == 201, res.text
    scanned_id = res.json()["scanned_document_id"]
    [key] = [k for k in bucket.objects if k.startswith("scans/")]
    assert key.endswith(f"/{scanned_id}/page-1.jpg")
    stored, content_type = bucket.objects[key]
    assert content_type == "image/jpeg" and len(stored) < len(upload)
    with Image.open(io.BytesIO(stored)) as image:  # A4 at 200 dpi, grayscale, aspect kept
        assert image.size == (1654, 2339) and image.mode == "L"

    client = env["client"]
    page = client.get(f"/api/validations/{scanned_id}/pages/1")
    assert page.status_code == 200 and page.content == stored
    assert page.headers["content-type"] == "image/jpeg"
    assert page.headers["cache-control"] == "no-store"
    detail = client.get(f"/api/validations/{scanned_id}")
    assert "supabase" not in detail.text  # no bucket URLs handed out

    student = env["login"]("maria.santos")
    link = student.post(f"/api/documents/{doc.id}/download-link").json()["url"]
    assert link.startswith("/api/files/")
    pdf = student.get(link)
    assert pdf.status_code == 200 and pdf.content == b"%PDF-1.7"
    assert pdf.headers["content-disposition"] == f'inline; filename="{doc.document_number}.pdf"'
    assert pdf.headers["content-type"] == "application/pdf"


def test_storage_outage_is_a_503_not_a_crash(bucket, env, monkeypatch):
    import httpx

    from app import storage as storage_module
    from tests.test_storage import KEY, URL

    scanned_id = submit(env, extracted_from(env["doc"])).json()["scanned_document_id"]

    def down(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    monkeypatch.setattr(storage_module, "_supabase", lambda *args: storage_module.SupabaseStorage(
        URL, KEY, "adgvs-private", transport=httpx.MockTransport(down)))
    res = env["client"].get(f"/api/validations/{scanned_id}/pages/1")
    assert res.status_code == 503
    assert res.json() == {"detail": "File storage is temporarily unavailable"}
    assert res.headers["X-Content-Type-Options"] == "nosniff"
