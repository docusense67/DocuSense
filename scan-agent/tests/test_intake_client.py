"""Intake API client: request shapes (backend/app/api/intake.py), retries, 4xx, 409."""

import json

import httpx
import numpy as np
import pytest

from scan_agent.client import (
    BackendClient,
    BackendUnreachable,
    BadResponse,
    IntakeRejected,
    result_json,
)
from tests.intake_server import FakeIntakeServer, form_parts

RESULT = {"schema": 1, "agent_version": "0.1.0", "quality": {"ok": True, "issues": []},
          "doc_type": {"predicted": "jhs_report_card", "confidence": 0.93},
          "checks": {"expected_headings_found": 3, "expected_headings_total": 3,
                     "layout_score": 0.9},
          "indicators": [], "timings_ms": {"capture": 900}}
JPEG = bytes.fromhex("ffd8ffe000104a464946")  # magic only: enough for the shape tests


def make(server=None, **kwargs):
    server = server or FakeIntakeServer()
    sleeps: list[float] = []
    client = BackendClient(station_key=server.key, http=server.client(), sleep=sleeps.append,
                           **kwargs)
    return server, client, sleeps


def record(handler):
    """A MockTransport that records every request and answers with `handler`."""
    seen: list[httpx.Request] = []

    def transport(request):
        seen.append(request)
        return handler(request)

    client = BackendClient(station_key="k", sleep=lambda s: None,
                           http=httpx.Client(base_url="https://x.example.edu.ph",
                                             transport=httpx.MockTransport(transport)))
    return client, seen


def test_upload_sends_the_contract_multipart_fields():
    def answer(request):
        return httpx.Response(201, json={"intake_id": 7, "decision": "manual_review", "tray": 2,
                                         "reasons": ["layout_score_low"]})

    client, seen = record(answer)
    decision = client.upload_intake(JPEG, RESULT, batch_id=3, sequence=12)

    assert (decision.intake_id, decision.decision, decision.tray) == (7, "manual_review", 2)
    assert decision.reasons == ["layout_score_low"]
    request = seen[0]
    assert request.method == "POST" and request.url.path == "/api/station/intake/documents"
    assert request.headers["X-Station-Key"] == "k"
    parts = form_parts(request)
    assert set(parts) == {"result", "batch_id", "sequence", "image"}
    assert json.loads(parts["result"][1]) == RESULT
    assert parts["batch_id"][1] == b"3" and parts["sequence"][1] == b"12"
    headers, image = parts["image"]
    assert image == JPEG and headers["Content-Type"] == "image/jpeg"
    assert 'filename="page.jpg"' in headers["Content-Disposition"]


def test_batch_calls_and_ids_are_integers():
    server, client, _ = make()
    batch_id = client.start_batch()
    assert batch_id == 1 and server.calls == ["POST /api/station/intake/batches"]
    summary = client.end_batch(batch_id)
    assert summary["total"] == 0 and summary["batch_id"] == 1
    assert client.end_batch(batch_id) == summary  # ending twice is harmless


def test_routed_report_shapes():
    client, seen = record(lambda r: httpx.Response(200, json={"intake_id": 5}))
    client.report_routed(5, tray=1)
    client.report_routed(5, jam=True, detail="Gate jam " + "x" * 300)
    assert seen[0].url.path == "/api/station/intake/documents/5/routed"
    assert json.loads(seen[0].content) == {"tray": 1, "jam": False}
    jam = json.loads(seen[1].content)
    assert jam["tray"] is None and jam["jam"] is True and len(jam["detail"]) == 200
    with pytest.raises(ValueError):
        client.report_routed(5)


def test_network_errors_and_5xx_are_retried_with_backoff():
    server, client, sleeps = make()
    server.down_paths["/batches"] = 2  # two connection errors, then it answers
    assert client.start_batch() == 1
    assert sleeps == [1.0, 2.0]

    answers = iter([httpx.Response(503), httpx.Response(502),
                    httpx.Response(201, json={"batch_id": 9})])
    client, seen = record(lambda r: next(answers))
    assert client.start_batch() == 9 and len(seen) == 3


def test_unreachable_after_every_attempt():
    server, client, sleeps = make(attempts=5, backoff=2.0)
    server.down = True
    with pytest.raises(BackendUnreachable, match="failed 5 time"):
        client.upload_intake(JPEG, RESULT)
    assert sleeps == [2.0, 4.0, 8.0, 8.0]  # capped


@pytest.mark.parametrize("code", [401, 404, 409, 413, 422])
def test_4xx_is_not_retried(code):
    server, client, sleeps = make()
    server.status_for["/documents"] = code
    with pytest.raises(IntakeRejected) as e:
        client.upload_intake(JPEG, RESULT)
    assert e.value.status_code == code and e.value.detail == f"forced {code}"
    assert sleeps == [] and len(server.calls) == 1


def test_a_different_second_routed_report_is_a_409_conflict():
    server, client, _ = make()
    batch_id = client.start_batch()
    decision = client.upload_intake(_real_jpeg(), RESULT, batch_id=batch_id, sequence=1)
    assert decision.decision == "passed_screening" and decision.tray == 1
    first = client.report_routed(decision.intake_id, tray=1)
    assert first["routed_tray"] == 1 and first["decision"] == "passed_screening"
    assert client.report_routed(decision.intake_id, tray=1) == first  # retried: same answer
    conflict = client.report_routed(decision.intake_id, jam=True)
    assert conflict == {"conflict": True, "detail": "Routing was already reported"}


def test_validation_errors_are_summarised():
    client, _ = record(lambda r: httpx.Response(
        422, json={"detail": [{"loc": ["body", "tray"], "msg": "tray required"}]}))
    with pytest.raises(IntakeRejected, match="HTTP 422: tray required"):
        client.report_routed(1, jam=True)


@pytest.mark.parametrize("body", [{"intake_id": "7", "decision": "manual_review", "tray": 2,
                                   "reasons": []},
                                  {"intake_id": 7, "decision": "authentic", "tray": 1,
                                   "reasons": []},
                                  {"intake_id": 7, "decision": "manual_review", "tray": 3,
                                   "reasons": []},
                                  [1, 2]])
def test_off_contract_answers_are_not_decisions(body):
    client, _ = record(lambda r: httpx.Response(201, json=body))
    with pytest.raises(BadResponse):
        client.upload_intake(JPEG, RESULT)


def test_result_json_converts_numpy_and_refuses_nan():
    text = result_json({"a": np.float32(0.5), "b": np.int64(3), "c": np.arange(2)})
    assert json.loads(text) == {"a": 0.5, "b": 3, "c": [0, 1]}
    with pytest.raises(ValueError):
        result_json({"blur_score": float("nan")})


def _real_jpeg() -> bytes:
    import cv2

    ok, buf = cv2.imencode(".jpg", np.full((80, 60), 230, np.uint8))
    assert ok
    return buf.tobytes()
