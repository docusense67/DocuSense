"""HTTPS client for the backend station API (authenticated with the station's API key).

Scan sessions (validation station): session / lookup / status / submit.
Intake screening (incoming-document station, docs/intake/screening-contract.md and
backend/app/api/intake.py): start_batch / upload_intake / report_routed / end_batch. Intake
calls are retried with backoff on network errors and 5xx (Render's free tier answers 502/503
while it wakes up), never on 4xx: a refused request does not get better by repeating it.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import httpx
import numpy as np

from scan_agent.pipeline import ExtractedDocument

INTAKE = "/api/station/intake"
DECISIONS = ("passed_screening", "manual_review")
MAX_BACKOFF_S = 8.0


class IntakeError(Exception):
    """An intake call failed. Messages are operator/log-facing and carry no personal data."""


class BackendUnreachable(IntakeError):
    """Network error or 5xx on every attempt: the backend could not be asked."""


class IntakeRejected(IntakeError):
    """The backend refused the request (4xx): repeating it will not help."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class BadResponse(IntakeError):
    """The backend answered 2xx with something that is not the contract's shape."""


@dataclass(frozen=True)
class IntakeDecision:
    """The backend's screening decision for one uploaded page."""

    intake_id: int
    decision: str  # passed_screening | manual_review
    tray: int  # 1 | 2
    reasons: list[str]


def _jsonable(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def result_json(result: dict) -> str:
    """The screening result as the `result` form field (numpy values converted; NaN and
    infinity refused, as the backend refuses them)."""
    return json.dumps(result, default=_jsonable, allow_nan=False, separators=(",", ":"))


def _detail(res: httpx.Response) -> str:
    try:
        detail = res.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):  # FastAPI validation errors: keep only the messages
        detail = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d)
                           for d in detail)
    return str(detail or res.reason_phrase or "error")[:300]


class BackendClient:
    def __init__(self, base_url: str | None = None, station_key: str = "",
                 http: httpx.Client | None = None, timeout: float = 30.0,
                 ca_cert: str | None = None, *, attempts: int = 4, backoff: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep):
        # `http` lets tests pass FastAPI's TestClient (an httpx.Client) directly.
        # `ca_cert`: the school's CA or self-signed server certificate (PEM), so TLS is
        # verified on a campus server whose certificate is not publicly trusted.
        # attempts/backoff/sleep: retries of the intake calls (not of the session calls).
        self.http = http or httpx.Client(base_url=base_url, timeout=timeout,
                                         verify=ca_cert or True)
        self.headers = {"X-Station-Key": station_key}
        self.attempts = max(1, attempts)
        self.backoff = backoff
        self.sleep = sleep

    def _post(self, path: str, **kwargs) -> dict:
        res = self.http.post(path, headers=self.headers, **kwargs)
        res.raise_for_status()
        return res.json()

    def session(self) -> dict:
        res = self.http.get("/api/station/session", headers=self.headers)
        res.raise_for_status()
        return res.json()

    def lookup(self, qr_token: str | None, document_number: str | None) -> dict:
        return self._post("/api/station/lookup",
                          json={"qr_token": qr_token, "document_number": document_number})

    def status(self, state: str, detail: str | None = None) -> None:
        self._post("/api/station/status", json={"state": state, "detail": detail})

    def submit(self, session_id: int, document: ExtractedDocument) -> dict:
        files = []
        for page in document.pages:
            ok, png = cv2.imencode(".png", page.image)
            if not ok:
                raise RuntimeError("Could not encode page image")
            files.append(("pages", (f"page-{page.sequence}.png", png.tobytes(), "image/png")))
        return self._post(f"/api/station/sessions/{session_id}/documents",
                          data={"payload": json.dumps(document.payload)}, files=files)

    # ---- intake screening -------------------------------------------------------------

    def _send(self, method: str, path: str, *, allow: tuple[int, ...] = (),
              **kwargs) -> httpx.Response:
        """One request with retries on network errors and 5xx. 4xx -> IntakeRejected,
        except the statuses in `allow`, which are returned to the caller."""
        last = ""
        for attempt in range(self.attempts):
            if attempt:
                self.sleep(min(self.backoff * 2 ** (attempt - 1), MAX_BACKOFF_S))
            try:
                res = self.http.request(method, path, headers=self.headers, **kwargs)
            except httpx.TransportError as e:
                last = f"{type(e).__name__}: {e}"
                continue
            if res.status_code >= 500:
                last = f"HTTP {res.status_code}"
                continue
            if res.status_code >= 400 and res.status_code not in allow:
                raise IntakeRejected(res.status_code, _detail(res))
            return res
        raise BackendUnreachable(f"{method} {path} failed {self.attempts} time(s): {last}")

    @staticmethod
    def _json(res: httpx.Response) -> dict:
        try:
            body = res.json()
        except ValueError:
            raise BadResponse(f"HTTP {res.status_code}: not JSON") from None
        if not isinstance(body, dict):
            raise BadResponse(f"HTTP {res.status_code}: not a JSON object")
        return body

    def start_batch(self) -> int:
        """A new mixed-document batch (S-10); the backend ends any batch left open."""
        body = self._json(self._send("POST", f"{INTAKE}/batches"))
        batch_id = body.get("batch_id")
        if isinstance(batch_id, bool) or not isinstance(batch_id, int):
            raise BadResponse("start batch: no integer batch_id")
        return batch_id

    def end_batch(self, batch_id: int) -> dict:
        """Batch summary (ending twice returns the same summary)."""
        return self._json(self._send("POST", f"{INTAKE}/batches/{batch_id}/end"))

    def upload_intake(self, image_jpeg: bytes, result: dict | str, *,
                      batch_id: int | None = None, sequence: int | None = None
                      ) -> IntakeDecision:
        """One screened page -> the backend's decision and tray."""
        data = {"result": result if isinstance(result, str) else result_json(result)}
        if batch_id is not None:
            data["batch_id"] = str(batch_id)
        if sequence is not None:
            data["sequence"] = str(sequence)
        res = self._send("POST", f"{INTAKE}/documents", data=data,
                         files={"image": ("page.jpg", image_jpeg, "image/jpeg")})
        body = self._json(res)
        intake_id, decision, tray = body.get("intake_id"), body.get("decision"), body.get("tray")
        reasons = body.get("reasons")
        if (isinstance(intake_id, bool) or not isinstance(intake_id, int)
                or decision not in DECISIONS or tray not in (1, 2)
                or not isinstance(reasons, list)):
            raise BadResponse("upload: response is not {intake_id, decision, tray, reasons}")
        return IntakeDecision(intake_id, decision, tray, [str(r) for r in reasons])

    def report_routed(self, intake_id: int, *, tray: int | None = None, jam: bool = False,
                      detail: str | None = None) -> dict:
        """What the gate actually did: `tray` it went to, or `jam`. A repeated identical
        report is accepted (200); a different second report is a 409, returned here as
        {"conflict": True, "detail": ...} (the backend keeps the first report)."""
        if tray is None and not jam:
            raise ValueError("report_routed needs a tray or jam=True")
        body: dict = {"tray": tray, "jam": jam}
        if detail:
            body["detail"] = detail[:200]
        res = self._send("POST", f"{INTAKE}/documents/{intake_id}/routed", json=body,
                         allow=(409,))
        if res.status_code == 409:
            return {"conflict": True, "detail": _detail(res)}
        return self._json(res)
