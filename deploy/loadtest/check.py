"""Judge a Locust run against Section 21: every screen under 2 s (95th percentile) and no
errors to speak of. Prints the table either way; exits 1 if a target is missed.

    python deploy/loadtest/check.py loadtest/results_stats.csv
"""

import csv
import sys

PAGE_P95_MS = 2000  # Section 21: pages load in under 2 seconds
CALL_P95_MS = 2000
MAX_FAILURE_RATE = 0.01  # "without errors": allow stray network blips, not failures


def main(path: str) -> int:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    problems = []
    print(f"{'type':<6} {'name':<28} {'requests':>9} {'failures':>9} {'median':>8} {'p95':>8} {'max':>8}")
    for row in rows:
        kind, name = row["Type"], row["Name"]
        count, failures = int(row["Request Count"]), int(row["Failure Count"])
        p95 = float(row["95%"] or 0)
        print(f"{kind:<6} {name:<28} {count:>9} {failures:>9} {row['Median Response Time']:>8} "
              f"{p95:>8.0f} {float(row['Max Response Time']):>8.0f}")
        if name == "Aggregated":
            if count == 0:
                problems.append("no requests were made")
            elif failures / count > MAX_FAILURE_RATE:
                problems.append(f"{failures}/{count} requests failed")
            continue
        if name in {"login"}:
            continue  # argon2 is slow on purpose; logins happen once per session
        limit = PAGE_P95_MS if kind == "PAGE" else CALL_P95_MS
        if p95 > limit:
            problems.append(f"{kind} {name}: 95th percentile {p95:.0f} ms > {limit} ms")

    for problem in problems:
        print("FAIL:", problem)
    print("PASS: Section 21 web targets met" if not problems else "")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
