"""Per-submission report of the applicant intake (docs/applicant-intake.md): JSON, CSV, PDF.

Staff only. It holds both OCR passes, the reason codes and findings of the assessment, the
reference and model versions, the processing attempts and the review decisions. Unlike the
audit log it contains what was read (field values), so it is served only to registrar staff.
"""

import csv
import html
import io
import json
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    ApplicantSecondPass,
    ApplicantSubmission,
    AuditLog,
    IntakeDocument,
    Student,
    User,
)
from app.services import intake as screening

NOTE = ("Passed screening means no automatic check flagged the document; needs manual review "
        "means at least one did. A match with a school's reference sample is not proof of "
        "authenticity: staff make the decision.")


def _email(db: Session, user_id: int | None) -> str | None:
    user = db.get(User, user_id) if user_id else None
    return user.email if user else None


def _fields(reading: dict | None) -> dict[str, Any]:
    out = {}
    for name, item in ((reading or {}).get("fields") or {}).items():
        out[name] = item.get("value") if isinstance(item, dict) else item
    return out


def second_pass_view(sp: ApplicantSecondPass | None, first_reading: dict | None) -> dict | None:
    """The second pass for staff: its status and, once done, where it disagrees with the
    first pass (with both values; staff see what was read)."""
    if sp is None:
        return None
    first, second = _fields(first_reading), _fields(sp.reading)
    return {
        "status": sp.status, "attempts": sp.attempts, "error": sp.error,
        "created_at": sp.created_at, "completed_at": sp.completed_at,
        "engine": (sp.reading or {}).get("engine"),
        "fields": second if sp.reading is not None else None,
        "disagreements": [{"field": name, "first": first.get(name), "second": second.get(name)}
                          for name in (sp.disagreements or [])],
    }


def history_filter(submission_id: int | None, intake_id: int | None):
    """Audit entries of an online submission and of its intake document."""
    conditions = []
    if submission_id is not None:
        conditions.append((AuditLog.entity_type == "applicant_submission")
                          & (AuditLog.entity_id == str(submission_id)))
    if intake_id is not None:
        conditions.append((AuditLog.entity_type == "intake_document")
                          & (AuditLog.entity_id == str(intake_id)))
    return or_(*conditions)


def _doc_type_block(reading: dict | None) -> dict | None:
    block = (reading or {}).get("doc_type")
    return block if isinstance(block, dict) else None


def build(db: Session, sub: ApplicantSubmission) -> dict:
    doc = db.get(IntakeDocument, sub.intake_document_id) if sub.intake_document_id else None
    result = (doc.screening_result if doc else None) or {}
    first = {k: v for k, v in result.items() if k != "assessment"} if doc else None
    assessment = result.get("assessment") if doc else None
    student = db.get(Student, doc.student_id) if doc and doc.student_id else None
    history = db.execute(
        select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.user_id)
        .where(history_filter(sub.id, doc.id if doc else None))
        .order_by(AuditLog.id)).all()
    reasons = list(doc.reasons) if doc else []
    return {
        "report": "applicant intake submission", "note": NOTE,
        "submission": {
            "id": sub.id, "applicant_email": _email(db, sub.applicant_id),
            "uploaded_by": _email(db, sub.uploaded_by), "declared_type": sub.declared_type,
            "issuing_school": sub.issuing_school, "status": sub.status,
            "pages": len(sub.pages), "consent_at": sub.consent_at,
            "submitted_at": sub.submitted_at, "attempts": sub.attempts, "error": sub.error,
        },
        "first_pass": first,
        "second_pass": second_pass_view(sub.second_pass, first),
        "decision": None if doc is None else {
            "intake_id": doc.id, "decision": doc.decision, "doc_type": doc.doc_type,
            "reasons": [{"code": r, "text": screening.REASON_TEXT.get(r, r)} for r in reasons],
        },
        "assessment": assessment,
        "versions": {
            "classifier": (_doc_type_block(first) or {}).get("model"),
            "classifier_evaluation": (_doc_type_block(first) or {}).get("evaluation"),
            "reference": (assessment or {}).get("reference"),
            "layout_model": ((assessment or {}).get("layout_model") or {}).get("version"),
        },
        "review": None if doc is None else {
            "status": doc.status, "reviewed_by": _email(db, doc.reviewed_by),
            "reviewed_at": doc.reviewed_at, "reason": doc.review_reason,
            "fields_reviewed_by": _email(db, doc.fields_reviewed_by),
            "fields_reviewed_at": doc.fields_reviewed_at,
            "reviewed_fields": doc.reviewed_fields,
            "student": None if student is None else {
                "id": student.id, "student_number": student.student_number},
            "linked_by": _email(db, doc.linked_by), "linked_at": doc.linked_at,
        },
        "history": [{"timestamp": log.timestamp, "action": log.action, "user_email": email,
                     "entity_type": log.entity_type, "details": log.new_value}
                    for log, email in history],
    }


def _jsonable(data: Any) -> Any:
    return json.loads(json.dumps(data, default=str))


def as_json(data: dict) -> dict:
    return _jsonable(data)


def _flatten(prefix: str, value: Any, rows: list[list[str]]) -> None:
    if isinstance(value, dict) and value:
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), item, rows)
    elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        for i, item in enumerate(value):
            _flatten(f"{prefix}[{i}]", item, rows)
    else:
        rows.append([prefix, value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False)])


def as_csv(data: dict) -> str:
    """One row per value: `section.key`, value (JSON for anything that isn't text)."""
    rows: list[list[str]] = []
    _flatten("", _jsonable(data), rows)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["item", "value"])
    writer.writerows(rows)
    return out.getvalue()


STYLE = ("body{font-family:sans-serif;font-size:9pt}h1{font-size:14pt}h2{font-size:11pt;"
         "margin-top:12pt}table{width:100%;border-collapse:collapse}td,th{border:1px solid #999;"
         "padding:3px;text-align:left;vertical-align:top}pre{white-space:pre-wrap;"
         "overflow-wrap:anywhere;font-size:8pt}")


def as_html(data: dict) -> str:
    """For the PDF renderer only (never served to a browser)."""
    data = _jsonable(data)
    sub = data["submission"]
    parts = [f"<html><head><style>{STYLE}</style></head><body>",
             f"<h1>Applicant submission #{sub['id']}</h1>",
             f"<p>{html.escape(data['note'])}</p>"]
    decision = data.get("decision")
    if decision:
        parts.append(f"<p><b>{html.escape(str(decision['decision']).replace('_', ' '))}</b></p>"
                     "<ul>" + "".join(f"<li>{html.escape(r['text'])} ({html.escape(r['code'])})"
                                      "</li>" for r in decision["reasons"]) + "</ul>")
    second = data.get("second_pass") or {}
    if second.get("disagreements"):
        rows = "".join(f"<tr><td>{html.escape(d['field'])}</td>"
                       f"<td>{html.escape(str(d['first']))}</td>"
                       f"<td>{html.escape(str(d['second']))}</td></tr>"
                       for d in second["disagreements"])
        parts.append("<h2>OCR passes disagree</h2><table><tr><th>Field</th><th>First pass</th>"
                     f"<th>Second pass</th></tr>{rows}</table>")
    for key in ("submission", "versions", "assessment", "review", "first_pass", "second_pass",
                "history"):
        serialized = html.escape(json.dumps(data.get(key), indent=2, ensure_ascii=False))
        parts.append(f"<h2>{html.escape(key.replace('_', ' '))}</h2><pre>{serialized}</pre>")
    parts.append("</body></html>")
    return "".join(parts)
