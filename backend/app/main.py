import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    applicant_intake,
    audit,
    auth,
    catalog,
    dashboard,
    documents,
    grades,
    intake,
    intake_operator,
    intake_profiles,
    internal,
    mechanism,
    notifications,
    requests,
    scanning,
    stations,
    students,
    templates,
    users,
)
from app.config import get_settings
from app.storage import StorageError

_docs = get_settings().api_docs
app = FastAPI(
    title="Academic Document Generation & Validation System",
    version="0.1.0",
    docs_url="/docs" if _docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _docs else None,
)

if get_settings().secret_key.startswith("dev-only"):
    logging.getLogger(__name__).warning(
        "ADGVS_SECRET_KEY is the development default; set a random value in production."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    """CSRF defence beyond SameSite=Strict, which trusts sibling subdomains (review L1).

    Browsers label every request with Sec-Fetch-Site (older ones with Origin); a write from
    another site is refused. The scan station is not a browser and sends neither header."""
    if request.method in UNSAFE_METHODS:
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        allowed_origins = {f"{request.url.scheme}://{request.headers.get('host', '')}",
                           *get_settings().cors_origins}
        if site is not None:
            cross = site not in {"same-origin", "none"}
        else:
            cross = origin is not None and origin not in allowed_origins
        if cross:
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Defaults for every API response; Nginx adds HSTS and the SPA's CSP (Section 18)."""
    response = await call_next(request)
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")  # registrar review frames the PDF
    if request.url.path.startswith("/api/"):
        headers.setdefault("Cache-Control", "no-store")  # responses carry personal data
    return response


app.include_router(auth.router, prefix="/api")
for module in (users, students, catalog, grades, templates, requests, documents, stations,
               scanning, intake, intake_profiles, intake_operator, dashboard, audit,
               internal, mechanism, notifications, applicant_intake):
    app.include_router(module.router, prefix="/api")


@app.exception_handler(StorageError)
async def storage_unavailable(request: Request, exc: StorageError):
    # The cause (HTTP status, network error) goes to the server log, not to the caller.
    logging.getLogger(__name__).error("File storage error on %s: %s", request.url.path, exc)
    return JSONResponse({"detail": "File storage is temporarily unavailable"}, status_code=503)


@app.get("/api/health", tags=["health"])
def health():
    # Render sets RENDER_GIT_COMMIT; deploy.yml waits until the new commit answers.
    commit = os.environ.get("RENDER_GIT_COMMIT")
    return {"status": "ok", "commit": commit} if commit else {"status": "ok"}
