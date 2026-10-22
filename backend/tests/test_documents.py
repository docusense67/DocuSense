"""Epic 3: mapping layer, identity, templates, generation."""

import time
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.config import get_settings
from app.documents import identity
from app.documents.context import (
    BuildOptions,
    MissingDataError,
    build_coe,
    build_tor,
    format_name,
    format_units,
    ordinal_year,
)
from app.documents.defaults import load_html
from app.documents.pdf import RenderedPdf
from app.documents.registry import PLACEHOLDER_CATALOG, REGISTRY
from app.documents.service import (
    GenerationError,
    active_template,
    generate_document,
    render_snapshot,
    validate_template,
)
from app.documents.templating import TemplateRenderError, check_template, render_html
from app.models import (
    AuditLog,
    DocumentRequest,
    DocumentTemplate,
    GeneratedDocument,
    Student,
    Term,
    User,
)
from app.models.enums import DocumentType, TemplateStatus
from app.seed import DEMO_PASSWORD, seed

ISSUED = date(2026, 10, 5)


def weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except OSError:
        return False
    return True


class FakeRenderer:
    def __init__(self, pages: int = 1):
        self.pages = pages
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, html: str, left: str, right: str) -> RenderedPdf:
        self.calls.append((html, left, right))
        return RenderedPdf(content=b"%PDF-1.7 fake", page_count=self.pages)


@pytest.fixture
def seeded(db):
    seed(db)
    for user in db.scalars(select(User)):
        user.must_change_password = False
    db.commit()
    return db


@pytest.fixture(autouse=True)
def tmp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path))
    return tmp_path


def student(db, number: str) -> Student:
    return db.scalar(select(Student).where(Student.student_number == number))


def make_request(db, number: str, doc_type: DocumentType, purpose="Employment"):
    count = db.scalar(select(func.count()).select_from(DocumentRequest))
    request = DocumentRequest(reference_no=f"REQ-{count + 1}", student_id=student(db, number).id,
                              document_type=doc_type, purpose=purpose)
    db.add(request)
    db.flush()
    return request


# ---- mapping layer --------------------------------------------------------------------------

def test_name_and_value_formatting():
    s = Student(first_name="Juan", middle_name="Dizon", last_name="Dela Cruz", suffix="Jr.",
                student_number="x")
    assert format_name(s) == "JUAN D. DELA CRUZ JR."
    s.middle_name = None
    s.suffix = None
    assert format_name(s) == "JUAN DELA CRUZ"
    assert [ordinal_year(n) for n in (1, 2, 3, 4, 11)] == [
        "1st Year", "2nd Year", "3rd Year", "4th Year", "11th Year"]
    assert format_units(Decimal("3.0")) == "3"
    assert format_units(Decimal("1.5")) == "1.5"


def test_tor_context_is_all_printed_strings(seeded):
    values = build_tor(seeded, student(seeded, "2025-00001"), BuildOptions(issued_on=ISSUED))
    assert values["student_name"] == "MARIA R. SANTOS"
    assert values["date_issued"] == "October 5, 2026"
    # Only the locked 1st semester is printed; the open 2nd semester is excluded.
    assert [t["semester"] for t in values["terms"]] == ["1st Semester"]
    assert values["gwa"] == "1.44"  # 17.25 points / 12 units
    it101 = next(s for s in values["terms"][0]["subjects"] if s["subject_code"] == "IT101")
    assert it101 == {"subject_code": "IT101", "subject_title": "Introduction to Computing",
                     "units": "3", "grade": "1.25", "remarks": "Passed"}


def test_tor_requires_a_locked_term(seeded):
    seeded.execute(update(Term).values(is_grades_locked=False))
    with pytest.raises(MissingDataError, match="locked"):
        build_tor(seeded, student(seeded, "2025-00001"), BuildOptions(issued_on=ISSUED))


def test_tor_context_keeps_inc_and_excludes_it_from_gwa(seeded):
    values = build_tor(seeded, student(seeded, "2025-00003"), BuildOptions(issued_on=ISSUED))
    grades = [s["grade"] for t in values["terms"] for s in t["subjects"]]
    assert "INC" in grades


def test_coe_context(seeded):
    values = build_coe(seeded, student(seeded, "2025-00001"),
                       BuildOptions(issued_on=ISSUED, purpose=" Employment "))
    assert values["year_level"] == "1st Year"
    assert values["semester"] == "2nd Semester"
    assert values["academic_year"] == "2025-2026"
    assert values["purpose"] == "Employment"


def test_coe_missing_data_lists_every_problem(seeded):
    seeded.execute(update(Term).values(is_current=False))
    maria = student(seeded, "2025-00001")
    maria.year_level = None
    with pytest.raises(MissingDataError) as exc:
        build_coe(seeded, maria, BuildOptions(issued_on=ISSUED, purpose=""))
    assert len(exc.value.missing) == 3


# ---- identity -------------------------------------------------------------------------------

def test_hash_is_independent_of_key_order():
    a = {"b": "2", "a": "1", "terms": [{"x": "y"}]}
    b = {"terms": [{"x": "y"}], "a": "1", "b": "2"}
    assert identity.snapshot_hash(a) == identity.snapshot_hash(b)
    assert len(identity.short_hash(identity.snapshot_hash(a))) == 12


def test_qr_is_svg_data_uri_and_payload_has_no_personal_data():
    token = identity.new_qr_token()
    assert identity.qr_payload(token) == f"ADGVS1:{token}"
    assert identity.qr_data_uri(identity.qr_payload(token)).startswith("data:image/svg+xml;base64,")


# ---- templating -----------------------------------------------------------------------------

def test_check_template_reports_unknown_and_missing():
    check = check_template("{{ student_name }} {{ grades_secret }}", {"student_name", "gwa"},
                           {"student_name", "gwa"})
    assert check.unknown_placeholders == ["grades_secret"]
    assert check.missing_placeholders == ["gwa"]


@pytest.mark.parametrize("html", [
    "<script>alert(1)</script>",
    '<img src="https://evil.example/x.png">',
    '<img src="file:///etc/passwd">',
    "<style>@import 'x.css';</style>",
    "<style>body{background:url(http://x/y.png)}</style>",
])
def test_check_template_rejects_remote_and_active_content(html):
    assert check_template(html, set(), set()).errors


def test_check_template_reports_syntax_errors():
    assert "syntax error" in check_template("{% for x in %}", set(), set()).errors[0]


def test_sandbox_blocks_python_internals():
    with pytest.raises(TemplateRenderError):
        render_html("{{ student_name.__class__.__mro__ }}", {"student_name": "x"})


def test_render_escapes_html_in_values():
    assert render_html("{{ student_name }}", {"student_name": "<b>X</b>"}) == "&lt;b&gt;X&lt;/b&gt;"


def test_default_templates_are_valid_and_use_only_catalog_names():
    for spec in REGISTRY.values():
        check = validate_template(spec.code, load_html(spec.default_html), spec.default_zones)
        assert check.can_activate, check
        assert set(check.placeholders) <= set(PLACEHOLDER_CATALOG)


def test_validation_requires_zones():
    check = validate_template(DocumentType.COE, load_html("coe.html"), None)
    assert not check.can_activate and "student_name" in check.missing_zones
    bad = {"fields": {"student_name": {"x": 200, "y": 10, "w": 50, "h": 5}}}
    assert "past the page edge" in validate_template(DocumentType.COE, "x", bad).errors[0]


# ---- generation -----------------------------------------------------------------------------

def test_generate_tor_freezes_snapshot_and_writes_pdf(seeded, tmp_storage):
    renderer = FakeRenderer(pages=2)
    request = make_request(seeded, "2025-00001", DocumentType.TOR)
    doc = generate_document(seeded, request, actor_id=None, issued_on=ISSUED, renderer=renderer)

    assert doc.document_number == "TOR-2026-000001"
    assert doc.page_count == 2
    assert doc.data_snapshot["student_name"] == "MARIA R. SANTOS"
    assert doc.data_hash == identity.snapshot_hash(doc.data_snapshot)
    assert (tmp_storage / doc.pdf_path).read_bytes().startswith(b"%PDF")

    html, left, right = renderer.calls[0]
    assert left == "TOR-2026-000001"
    assert right == f"Ref {doc.data_hash[:12]}"
    assert "MARIA R. SANTOS" in html and "data:image/svg+xml;base64," in html
    assert doc.qr_token not in html  # token only lives inside the QR image
    seeded.flush()
    assert seeded.scalar(select(AuditLog).where(AuditLog.action == "document_generated"))


def test_document_numbers_increment_per_type(seeded):
    r = FakeRenderer()
    first = generate_document(seeded, make_request(seeded, "2025-00001", DocumentType.COE),
                              actor_id=None, issued_on=ISSUED, renderer=r)
    second = generate_document(seeded, make_request(seeded, "2025-00002", DocumentType.COE),
                               actor_id=None, issued_on=ISSUED, renderer=r)
    tor = generate_document(seeded, make_request(seeded, "2025-00001", DocumentType.TOR),
                            actor_id=None, issued_on=ISSUED, renderer=r)
    assert [first.document_number, second.document_number, tor.document_number] == [
        "COE-2026-000001", "COE-2026-000002", "TOR-2026-000001"]
    assert first.qr_token != second.qr_token


def test_generation_fails_cleanly_without_active_template(seeded):
    seeded.execute(update(DocumentTemplate).values(status=TemplateStatus.RETIRED))
    with pytest.raises(GenerationError):
        generate_document(seeded, make_request(seeded, "2025-00001", DocumentType.TOR),
                          actor_id=None, renderer=FakeRenderer())


def test_generation_writes_nothing_when_data_missing(seeded):
    request = make_request(seeded, "2025-00001", DocumentType.COE, purpose="  ")
    with pytest.raises(MissingDataError):
        generate_document(seeded, request, actor_id=None, renderer=FakeRenderer())
    assert seeded.scalar(select(func.count()).select_from(GeneratedDocument)) == 0


@pytest.mark.skipif(not weasyprint_available(), reason="WeasyPrint system libraries missing")
def test_real_render_tor_and_single_page_coe(seeded):
    tor = generate_document(seeded, make_request(seeded, "2025-00001", DocumentType.TOR),
                            actor_id=None, issued_on=ISSUED)
    coe = generate_document(seeded, make_request(seeded, "2025-00001", DocumentType.COE),
                            actor_id=None, issued_on=ISSUED)
    assert tor.page_count >= 1
    assert coe.page_count == 1


@pytest.mark.skipif(not weasyprint_available(), reason="WeasyPrint system libraries missing")
def test_generation_meets_section21_time_targets(seeded, capsys):
    """Section 21: COE PDF in under 5 s, a 4-page TOR in under 10 s (measured on CI)."""
    start = time.perf_counter()
    generate_document(seeded, make_request(seeded, "2025-00001", DocumentType.COE),
                      actor_id=None, issued_on=ISSUED)
    coe_seconds = time.perf_counter() - start

    context = REGISTRY[DocumentType.TOR].sample_context()
    subjects = context["terms"][0]["subjects"] * 2
    context["terms"] = [
        {**context["terms"][0], "academic_year": f"{2020 + i // 2}-{2021 + i // 2}",
         "semester": ("1st Semester", "2nd Semester")[i % 2], "subjects": subjects}
        for i in range(12)
    ]
    start = time.perf_counter()
    pdf = render_snapshot(active_template(seeded, DocumentType.TOR).html_content,
                          {**context, "document_number": "TOR-TIMING"}, "TOR-TIMING", "TIMING")
    tor_seconds = time.perf_counter() - start

    with capsys.disabled():
        print(f"\nCOE {coe_seconds:.2f} s, {pdf.page_count}-page TOR render {tor_seconds:.2f} s")
    assert pdf.page_count >= 4
    assert coe_seconds < 5
    assert tor_seconds < 10


# ---- template API ---------------------------------------------------------------------------

@pytest.fixture
def registrar_client(client, seeded):
    res = client.post("/api/auth/login", json={"email": "registrar@school.edu.ph",
                                               "password": DEMO_PASSWORD})
    assert res.status_code == 200
    return client


def test_seed_activates_default_templates(registrar_client):
    templates = registrar_client.get("/api/templates").json()
    assert {(t["document_type"], t["status"]) for t in templates} == {
        ("TOR", "active"), ("COE", "active")}
    assert all(t["validation"]["can_activate"] for t in templates)


def test_upload_invalid_template_cannot_activate(registrar_client):
    res = registrar_client.post("/api/templates", json={
        "document_type": "COE", "html_content": "<p>{{ student_name }} {{ ssn }}</p>"})
    assert res.status_code == 201
    body = res.json()
    assert body["version"] == 2 and body["status"] == "draft"
    assert body["validation"]["unknown_placeholders"] == ["ssn"]
    assert not body["validation"]["can_activate"]
    assert registrar_client.post(f"/api/templates/{body['id']}/activate").status_code == 409


def test_activate_new_version_retires_old(registrar_client, seeded):
    html = load_html("coe.html").replace("CERTIFICATE OF ENROLLMENT", "CERTIFICATION")
    new = registrar_client.post("/api/templates", json={
        "document_type": "COE", "html_content": html,
        "field_zones": REGISTRY[DocumentType.COE].default_zones}).json()
    res = registrar_client.post(f"/api/templates/{new['id']}/activate")
    assert res.status_code == 200 and res.json()["status"] == "active"
    statuses = {t["version"]: t["status"]
                for t in registrar_client.get("/api/templates?document_type=COE").json()}
    assert statuses == {1: "retired", 2: "active"}
    # Active/retired versions are read-only.
    assert registrar_client.patch(f"/api/templates/{new['id']}",
                                  json={"html_content": "x"}).status_code == 409


def test_preview_returns_pdf(registrar_client, seeded, monkeypatch):
    monkeypatch.setattr("app.documents.service.render_pdf", FakeRenderer(pages=3))
    template_id = registrar_client.get("/api/templates?document_type=TOR").json()[0]["id"]
    maria = student(seeded, "2025-00001")
    res = registrar_client.post(f"/api/templates/{template_id}/preview?student_id={maria.id}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["x-page-count"] == "3"
    assert seeded.scalar(select(func.count()).select_from(GeneratedDocument)) == 0


def test_document_types_endpoint_lists_requirements(registrar_client):
    body = registrar_client.get("/api/document-types").json()
    tor = next(t for t in body["types"] if t["code"] == "TOR")
    assert "grades_table" in tor["required_zones"]
    assert "terms" in body["placeholder_catalog"]


def test_teacher_cannot_manage_templates(client, seeded):
    client.post("/api/auth/login", json={"email": "teacher@school.edu.ph",
                                         "password": DEMO_PASSWORD})
    assert client.get("/api/templates").status_code == 403
