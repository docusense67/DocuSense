"""The PlantUML use case diagrams (docs/diagrams/use-cases*.puml) agree with the access matrix.

Each detail diagram tags its use cases with the API routes behind them
(`' @routes <alias>: METHOD path | ...`). This test checks, against tests/test_access_matrix.py:

- every actor linked to a use case may call all of its routes (no link the API would refuse);
- every route a role may call is drawn as a use case of that role (or of a role it specialises);
- every API route except the health check and the scheduler's cron route is in some use case;
- the overview's actor-package links are exactly the actor links of the detail diagrams.

A use case that <<extend>>s another is also performed by the actors of the one it extends.
"""

import re
from collections import defaultdict
from pathlib import Path

import pytest

from tests import test_access_matrix as matrix

DIAGRAMS = Path(__file__).resolve().parents[2] / "docs" / "diagrams"
OVERVIEW = DIAGRAMS / "use-cases.puml"
DETAILS = sorted(DIAGRAMS.glob("use-cases-*.puml"))

NOT_A_USE_CASE = {"GET /api/health"} | matrix.INTERNAL  # probe; scheduler (no actor)

# Routes a role gets past the role check on but can never use: the download routes are open to
# every signed-in user and then refuse everyone but registrars and the owning student
# (api/documents.py `_can_download`).
# "Class adviser" is not a role either: every teacher may call GET /api/advisory, which lists
# only the classes they advise (section_advisers), so it is drawn for the adviser.
DOWNLOAD = {"POST /api/documents/{document_id}/download-link", "GET /api/files/{token}"}
ROW_LEVEL_ONLY = {"Teacher": DOWNLOAD | {"GET /api/advisory"}, "Adviser": DOWNLOAD,
                  "Admin": DOWNLOAD, "Applicant": DOWNLOAD}

ROLES = ("Student", "Teacher", "Registrar", "Admin")
ALLOWED = {
    "Student": matrix.STUDENT,
    "Teacher": matrix.TEACHER,
    "Adviser": matrix.TEACHER,  # a class adviser is a teacher with an advisory class
    "Registrar": matrix.CALLERS["registrar-no-validation"],
    "Operator": matrix.CALLERS["registrar"],  # registrar with the validation permission
    "Admin": matrix.ADMIN,
    "Applicant": matrix.APPLICANT,  # not yet a student (docs/applicant-intake.md)
    "ScanStation": matrix.STATION,
    "IntakeStation": matrix.STATION,
}
ALLOWED["AnyUser"] = set.intersection(*(set(ALLOWED[r]) for r in ROLES))
ALLOWED = {a: set(r) | (matrix.PUBLIC if a not in {"ScanStation", "IntakeStation"} else set())
           for a, r in ALLOWED.items()}

ACTOR = re.compile(r'^\s*actor\s+"[^"]*"\s+as\s+(\w+)', re.M)
USECASE = re.compile(r'^\s*usecase\s+"[^"]*"\s+as\s+(\w+)', re.M)
ROUTES = re.compile(r"^\s*' @routes (\w+): (.+)$", re.M)
PACKAGE = re.compile(r'^\s*package\s+"([^"]+)"(?:\s+as\s+(\w+))?', re.M)
LINK = re.compile(r"^\s*(\w+)\s+--\s+(\w+)\s*$", re.M)
GENERALISATION = re.compile(r"^\s*(\w+)\s+-(?:\w+-)?\|>\s+(\w+)\s*$", re.M)
EXTEND_LEFT = re.compile(r"^\s*(\w+)\s+<\.\.\s+(\w+)\s*:\s*<<extend>>", re.M)  # base <.. ext
EXTEND_RIGHT = re.compile(r"^\s*(\w+)\s+\.(?:\w+\.)?\.>\s+(\w+)\s*:\s*<<extend>>", re.M)


class Model:
    def __init__(self):
        self.routes: dict[str, set[str]] = {}
        self.package: dict[str, str] = {}  # use case -> package title
        self.direct: dict[str, set[str]] = defaultdict(set)  # use case -> linked actors
        self.extends: dict[str, set[str]] = defaultdict(set)  # extension -> bases
        self.parents: dict[str, set[str]] = defaultdict(set)  # actor -> generalisations
        self.actors: set[str] = set()


def _parse() -> Model:
    model = Model()
    for path in DETAILS:
        text = path.read_text(encoding="utf-8")
        titles = PACKAGE.findall(text)
        assert len(titles) == 1, f"{path.name}: one package per detail diagram"
        actors = set(ACTOR.findall(text))
        model.actors |= actors
        cases = USECASE.findall(text)
        for case in cases:
            assert case not in model.package, f"use case alias {case} reused ({path.name})"
            model.package[case] = titles[0][0]
            model.routes[case] = set()
        for case, spec in ROUTES.findall(text):
            assert case in cases, f"{path.name}: @routes for unknown use case {case}"
            model.routes[case] = {r.strip() for r in spec.split("|")}
        for a, b in LINK.findall(text):
            actor, case = (a, b) if a in actors else (b, a)
            assert actor in actors and case in cases, f"{path.name}: bad link {a} -- {b}"
            model.direct[case].add(actor)
        for child, parent in GENERALISATION.findall(text):
            model.parents[child].add(parent)
        for base, ext in EXTEND_LEFT.findall(text):
            model.extends[ext].add(base)
        for ext, base in EXTEND_RIGHT.findall(text):
            model.extends[ext].add(base)
    return model


@pytest.fixture(scope="module")
def model() -> Model:
    return _parse()


def _performers(model: Model, case: str, seen: frozenset = frozenset()) -> set[str]:
    actors = set(model.direct[case])
    for base in model.extends[case] - seen:
        actors |= _performers(model, base, seen | {case})
    return actors


def _ancestors(model: Model, actor: str) -> set[str]:
    found = {actor}
    for parent in model.parents[actor]:
        found |= _ancestors(model, parent)
    return found


def _app_routes() -> set[str]:
    return {f"{m} {p}" for m, p in matrix.routes()}


def test_details_exist():
    assert OVERVIEW.exists()
    assert len(DETAILS) >= 6


def test_known_actors_only(model):
    assert model.actors <= set(ALLOWED), model.actors - set(ALLOWED)


def test_every_route_named_exists(model):
    named = set().union(*model.routes.values())
    assert named - _app_routes() == set()


def test_every_use_case_with_routes_has_an_actor(model):
    orphans = [c for c, r in model.routes.items() if r and not _performers(model, c)]
    assert orphans == []


def test_linked_actors_may_call_every_route_of_their_use_cases(model):
    refused = sorted(
        f"{actor} -- {case}: {route}"
        for case, routes in model.routes.items()
        for actor in _performers(model, case)
        for route in routes - ALLOWED[actor]
    )
    assert refused == []


@pytest.mark.parametrize("actor", ["Student", "Teacher", "Adviser", "Registrar", "Operator",
                                   "Admin", "Applicant"])
def test_every_route_of_a_role_is_drawn_for_it(model, actor):
    roles = _ancestors(model, actor)
    covered = set().union(*(routes for case, routes in model.routes.items()
                            if _performers(model, case) & roles))
    expected = ALLOWED[actor] - NOT_A_USE_CASE - ROW_LEVEL_ONLY.get(actor, set())
    assert sorted(expected - covered) == []


def test_station_routes_are_drawn(model):
    stations = {"ScanStation", "IntakeStation"}
    covered = set().union(*(r for c, r in model.routes.items()
                            if _performers(model, c) & stations))
    assert covered == matrix.STATION


def test_every_api_route_is_in_a_use_case(model):
    named = set().union(*model.routes.values())
    assert sorted(_app_routes() - NOT_A_USE_CASE - named) == []


def test_overview_links_match_details(model):
    text = OVERVIEW.read_text(encoding="utf-8")
    packages = {alias: title for title, alias in PACKAGE.findall(text)}
    assert set(packages.values()) == set(model.package.values())
    actors = set(ACTOR.findall(text))
    drawn = set()
    for a, b in LINK.findall(text):
        actor, pkg = (a, b) if a in actors else (b, a)
        assert actor in actors and pkg in packages, f"bad overview link {a} -- {b}"
        drawn.add((actor, packages[pkg]))
    # Links of the abstract "signed-in user" are described by a note in the overview.
    expected = {(actor, model.package[case]) for case in model.package
                for actor in model.direct[case] if actor != "AnyUser"}
    assert sorted(drawn ^ expected) == []
    overview_parents = {(c, p) for c, p in GENERALISATION.findall(text)}
    detail_parents = {(c, p) for c, ps in model.parents.items() for p in ps if p != "AnyUser"}
    assert overview_parents == detail_parents


def test_screening_wording():
    for path in DIAGRAMS.glob("use-cases*.puml"):
        if "intake" not in path.name and path != OVERVIEW:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in ("authentic", "genuine", "verified"):
            assert word not in text, f"{path.name}: '{word}' (say passed screening)"


def test_every_source_has_exports():
    for source in [*DIAGRAMS.glob("*.puml"), *DIAGRAMS.glob("*.mmd")]:
        for suffix in (".svg", ".png"):
            target = source.with_suffix(suffix)
            assert target.exists(), f"run docs/diagrams/render.py ({source.name})"
