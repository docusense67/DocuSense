"""Section 21 load test: 100 simultaneous web users, typical screens under 2 seconds.

    locust -f deploy/loadtest/locustfile.py --headless -u 100 -r 5 -t 3m \
        --host https://adgvs.school.edu.ph --csv loadtest/results
    python deploy/loadtest/check.py loadtest/results_stats.csv

Run it against the real stack (Nginx + API + Postgres), never against production during
office hours. Accounts come from environment variables (defaults are the CI demo accounts):
LOAD_STUDENT_EMAIL / LOAD_STUDENT_PASSWORD, LOAD_REGISTRAR_EMAIL / LOAD_REGISTRAR_PASSWORD.
LOAD_CA_CERT: the server certificate to trust when it is self-signed or campus-issued.
The registrar account needs the scan operator permission for the operator screens.

Each "PAGE ..." entry is one screen: the calls the web app makes when it opens, made one
after another (a browser makes them in parallel, so the real page is faster than this).
"""

import os
import time

from locust import HttpUser, between, events, task

STUDENT = (os.environ.get("LOAD_STUDENT_EMAIL", "maria.santos@school.edu.ph"),
           os.environ.get("LOAD_STUDENT_PASSWORD", "Changed-In-CI-2026"))
REGISTRAR = (os.environ.get("LOAD_REGISTRAR_EMAIL", "registrar@school.edu.ph"),
             os.environ.get("LOAD_REGISTRAR_PASSWORD", "Changed-In-CI-2026"))


class Portal(HttpUser):
    abstract = True
    account: tuple[str, str]
    # People read a screen before clicking on; 3-10 s between screens is generous load.
    wait_time = between(3, 10)

    def on_start(self):
        # Locust ignores REQUESTS_CA_BUNDLE; a campus or self-signed certificate is given here.
        if ca_cert := os.environ.get("LOAD_CA_CERT"):
            self.client.verify = ca_cert
        email, password = self.account
        with self.client.post("/api/auth/login", json={"email": email, "password": password},
                              name="login", catch_response=True) as res:
            if res.status_code != 200:
                res.failure(f"login {res.status_code}: {res.text[:100]}")
            elif res.json().get("must_change_password"):
                res.failure("account still needs its first password change")

    def page(self, name: str, *paths: str) -> None:
        """Open a screen: the HTML shell, then each API call it makes; timed as one page."""
        start = time.perf_counter()
        ok = self.client.get("/", name="static /").ok
        for path in paths:
            ok = self.client.get(path, name=path.split("?")[0]).ok and ok
        events.request.fire(request_type="PAGE", name=name,
                            response_time=(time.perf_counter() - start) * 1000,
                            response_length=0, exception=None if ok else Exception("call failed"),
                            context={})


class Student(Portal):
    """Peak-period students checking their requests (most of the load)."""
    weight = 8
    account = STUDENT

    @task
    def my_documents(self):
        self.page("student home", "/api/auth/me", "/api/requests/eligibility", "/api/requests",
                  "/api/students/me/records")


class Registrar(Portal):
    weight = 2
    account = REGISTRAR

    @task(3)
    def dashboard(self):
        self.page("registrar dashboard", "/api/auth/me", "/api/dashboard/registrar")

    @task(2)
    def request_queue(self):
        self.page("request list", "/api/auth/me", "/api/requests?status=requested",
                  "/api/students")

    @task(1)
    def audit_trail(self):
        self.page("audit trail", "/api/auth/me", "/api/audit-logs?limit=50")

    @task(2)
    def scan_station(self):
        self.page("scan station", "/api/auth/me", "/api/scan-stations",
                  "/api/validations?limit=50", "/api/validations/stats")
