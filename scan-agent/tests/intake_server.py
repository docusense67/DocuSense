"""A stand-in for the backend's station intake API (backend/app/api/intake.py) behind
httpx.MockTransport: same routes, status codes and shapes, the contract's default decision
rules, and switches to simulate an unreachable or refusing server."""

import json
import math
from email.parser import BytesParser
from email.policy import HTTP

import cv2
import httpx
import numpy as np

STATION_STATES = {"ready", "scanning", "jam", "double_feed", "offline"}  # backend scanning.py
KNOWN_TYPES = {"psa_birth_certificate", "jhs_report_card", "good_moral_certificate",
               "jhs_completion_certificate"}


class Malformed(ValueError):
    pass


def _unit(value, where):
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) \
            or not 0 <= value <= 1:
        raise Malformed(f"{where} must be a number between 0 and 1")
    return value


def decide(result: dict) -> list[str]:
    """Contract defaults (docs/intake/screening-contract.md, S-04). Per the contract,
    `checks` is omitted when doc_type.predicted is unknown."""
    if result.get("schema") != 1:
        raise Malformed("result.schema must be 1")
    quality, doc_type = result.get("quality"), result.get("doc_type")
    if not isinstance(quality, dict) or not isinstance(quality.get("ok"), bool):
        raise Malformed("result.quality.ok must be true or false")
    if not isinstance(doc_type, dict) or not isinstance(doc_type.get("predicted"), str):
        raise Malformed("result.doc_type.predicted must be a document type name")
    confidence = _unit(doc_type.get("confidence"), "result.doc_type.confidence")
    known = doc_type["predicted"] in KNOWN_TYPES
    indicators = result.get("indicators")
    if not isinstance(indicators, list):
        raise Malformed("result.indicators must be a list")
    severities = [_unit(i.get("severity"), "severity") for i in indicators]
    checks = result.get("checks")
    if known and not isinstance(checks, dict):
        raise Malformed("result.checks must be an object")
    reasons = []
    if not quality["ok"]:
        reasons.append("quality_not_ok")
    if not known:
        reasons.append("doc_type_unknown")
    if confidence < 0.75:
        reasons.append("doc_type_low_confidence")
    if any(s >= 0.5 for s in severities):
        reasons.append("severe_indicator")
    if known:
        if _unit(checks.get("layout_score"), "layout_score") < 0.70:
            reasons.append("layout_score_low")
        similarity = (result.get("similarity") or {}).get("score")
        if similarity is not None and _unit(similarity, "similarity") < 0.60:
            reasons.append("similarity_low")
        if checks["expected_headings_found"] < checks["expected_headings_total"]:
            reasons.append("expected_headings_missing")
    return reasons


def form_parts(request: httpx.Request) -> dict[str, tuple[dict, bytes]]:
    """multipart/form-data -> {name: (headers, payload bytes)}."""
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    message = BytesParser(policy=HTTP).parsebytes(head + request.read())
    parts = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        parts[name] = (dict(part.items()), part.get_payload(decode=True))
    return parts


class FakeIntakeServer:
    def __init__(self, key: str = "station-key"):
        self.key = key
        self.batches: dict[int, dict] = {}
        self.docs: dict[int, dict] = {}
        self.statuses: list[tuple[str, str | None]] = []
        self.calls: list[str] = []  # "METHOD path" of every request that reached the server
        self.images: list[np.ndarray] = []
        self.results: list[dict] = []
        self.down = False  # every request: network error
        self.down_paths: dict[str, int] = {}  # path fragment -> requests left to fail
        self.status_for: dict[str, int] = {}  # path fragment -> forced HTTP status
        self.transport = httpx.MockTransport(self.handle)

    def client(self) -> httpx.Client:
        return httpx.Client(base_url="https://adgvs.example.edu.ph", transport=self.transport)

    # ---- routing -------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.down:
            raise httpx.ConnectError("network down", request=request)
        for fragment, left in self.down_paths.items():
            if fragment in path and left:
                self.down_paths[fragment] = left - 1
                raise httpx.ConnectError("network down", request=request)
        for fragment, code in self.status_for.items():
            if fragment in path:
                self.calls.append(f"{request.method} {path}")
                return httpx.Response(code, json={"detail": f"forced {code}"})
        self.calls.append(f"{request.method} {path}")
        if request.headers.get("X-Station-Key") != self.key:
            return httpx.Response(401, json={"detail": "Invalid station key"})
        parts = path.strip("/").split("/")
        if path == "/api/station/status":
            body = json.loads(request.content)
            if body["state"] not in STATION_STATES:
                return httpx.Response(422, json={"detail": "Unknown scanner state"})
            self.statuses.append((body["state"], body.get("detail")))
            return httpx.Response(200, json={"ok": True})
        if parts[:3] != ["api", "station", "intake"] or request.method != "POST":
            return httpx.Response(404, json={"detail": "Not Found"})
        rest = parts[3:]
        if rest == ["batches"]:
            return self.start_batch()
        if len(rest) == 3 and rest[0] == "batches" and rest[2] == "end":
            return self.end_batch(int(rest[1]))
        if rest == ["documents"]:
            return self.submit(request)
        if len(rest) == 3 and rest[0] == "documents" and rest[2] == "routed":
            return self.routed(int(rest[1]), json.loads(request.content))
        return httpx.Response(404, json={"detail": "Not Found"})

    # ---- handlers (mirror backend/app/api/intake.py) -------------------------------

    def start_batch(self):
        for batch in self.batches.values():
            batch["ended"] = True
        batch_id = len(self.batches) + 1
        self.batches[batch_id] = {"ended": False, "docs": []}
        return httpx.Response(201, json={"batch_id": batch_id})

    def summary(self, batch_id):
        docs = [self.docs[i] for i in self.batches[batch_id]["docs"]]
        return {"batch_id": batch_id, "started_at": "2026-10-12T08:00:00Z",
                "ended_at": "2026-10-12T08:05:00Z", "total": len(docs),
                "passed_screening": sum(d["decision"] == "passed_screening" for d in docs),
                "manual_review": sum(d["decision"] == "manual_review" for d in docs),
                "routed": sum(d["routed"] for d in docs),
                "unrouted": sum(not d["routed"] for d in docs),
                "routing_issues": sum(d["jam"] or (d["routed_tray"] is not None
                                                   and d["routed_tray"] != d["tray"])
                                      for d in docs)}

    def end_batch(self, batch_id):
        if batch_id not in self.batches:
            return httpx.Response(404, json={"detail": "Batch not found"})
        self.batches[batch_id]["ended"] = True
        return httpx.Response(200, json=self.summary(batch_id))

    def submit(self, request):
        parts = form_parts(request)
        try:
            result = json.loads(parts["result"][1],
                                parse_constant=lambda c: (_ for _ in ()).throw(Malformed(c)))
            reasons = decide(result)
        except (Malformed, json.JSONDecodeError, KeyError) as e:
            return httpx.Response(422, json={"detail": str(e)})
        batch_id = int(parts["batch_id"][1]) if "batch_id" in parts else None
        if batch_id is not None:
            if batch_id not in self.batches:
                return httpx.Response(404, json={"detail": "Batch not found"})
            if self.batches[batch_id]["ended"]:
                return httpx.Response(409, json={"detail": "Batch has ended"})
        headers, image = parts["image"]
        if not image.startswith(b"\xff\xd8\xff"):
            return httpx.Response(422, json={"detail": "image must be a JPEG or PNG"})
        decoded = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_UNCHANGED)
        if decoded is None:
            return httpx.Response(422, json={"detail": "unreadable image"})
        self.images.append(decoded)
        self.results.append(result)
        decision = "manual_review" if reasons else "passed_screening"
        intake_id = len(self.docs) + 1
        self.docs[intake_id] = {
            "batch_id": batch_id, "sequence": int(parts["sequence"][1]) if "sequence" in parts
            else None, "decision": decision, "tray": 2 if reasons else 1, "reasons": reasons,
            "routed": False, "routed_tray": None, "jam": False, "detail": None,
            "content_type": headers.get("Content-Type")}
        if batch_id is not None:
            self.batches[batch_id]["docs"].append(intake_id)
        return httpx.Response(201, json={"intake_id": intake_id, "decision": decision,
                                         "tray": 2 if reasons else 1, "reasons": reasons})

    def routed(self, intake_id, body):
        doc = self.docs.get(intake_id)
        if doc is None:
            return httpx.Response(404, json={"detail": "Intake document not found"})
        tray, jam = body.get("tray"), bool(body.get("jam"))
        if tray not in (None, 1, 2) or (tray is None and not jam):
            return httpx.Response(422, json={"detail": [{"msg": "tray (1 or 2) is required "
                                                                "unless jam is true"}]})
        jam = jam or tray is None
        if doc["routed"]:
            if doc["routed_tray"] == tray and doc["jam"] == jam:
                return httpx.Response(200, json=self._routed_out(intake_id))
            return httpx.Response(409, json={"detail": "Routing was already reported"})
        doc.update(routed=True, routed_tray=tray, jam=jam, detail=body.get("detail"))
        added = ["routing_jam"] if jam else (["routed_tray_mismatch"] if tray != doc["tray"]
                                             else [])
        if added:
            doc["decision"] = "manual_review"
            doc["reasons"] = doc["reasons"] + [r for r in added if r not in doc["reasons"]]
        return httpx.Response(200, json=self._routed_out(intake_id))

    def _routed_out(self, intake_id):
        doc = self.docs[intake_id]
        return {"intake_id": intake_id, "decision": doc["decision"], "assigned_tray": doc["tray"],
                "routed_tray": doc["routed_tray"], "jam": doc["jam"], "reasons": doc["reasons"]}
