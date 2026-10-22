"""In-memory feeder and gate for tests and dry runs (`intake --hardware fake`)."""

from collections.abc import Iterable

from scan_agent.hardware.base import (
    FEED_EMPTY,
    FEED_FED,
    FEED_JAM,
    ROUTE_JAM,
    ROUTE_OK,
    Fed,
    Feeder,
    Gate,
    Routed,
    check_tray,
)


class Compartment:
    """The camera compartment shared by the fake feeder and gate."""

    def __init__(self):
        self.sheet: int | None = None  # number of the sheet in it (1-based feed order)


class FakeFeeder(Feeder):
    """Feeds `sheets` sheets, then reports empty. `script` overrides single feeds by their
    1-based call number, e.g. {3: "jam"}."""

    name = "fake"

    def __init__(self, sheets: int = 0, *, compartment: Compartment | None = None,
                 script: dict[int, str] | None = None):
        self.remaining = sheets
        self.compartment = compartment or Compartment()
        self.script = dict(script or {})
        self.calls = 0
        self.fed = 0
        self.closed = False

    def feed_one(self) -> Fed:
        self.calls += 1
        scripted = self.script.get(self.calls)
        if scripted == FEED_JAM:
            return Fed(FEED_JAM, "Feeder jam (simulated)")
        if self.compartment.sheet is not None:
            return Fed(FEED_JAM, "Compartment not clear")
        if scripted == FEED_EMPTY or self.remaining <= 0:
            return Fed(FEED_EMPTY, "Feeder empty")
        self.remaining -= 1
        self.fed += 1
        self.compartment.sheet = self.fed
        return Fed(FEED_FED)

    def sheet_present(self) -> bool:
        return self.compartment.sheet is not None

    def paper_loaded(self) -> bool:
        return self.remaining > 0

    def close(self) -> None:
        self.closed = True


class FakeGate(Gate):
    """Routes to the asked tray. `outcomes` (in call order) can make a route jam ("jam") or
    land in another tray (1/2: a routing mismatch); `stuck` keeps the sheet in the
    compartment after an ok route."""

    name = "fake"

    def __init__(self, compartment: Compartment, outcomes: Iterable[str | int] = (),
                 *, stuck: bool = False):
        self.compartment = compartment
        self.outcomes = list(outcomes)
        self.stuck = stuck
        self.routes: list[tuple[int | None, int | None]] = []  # (sheet, tray it went to)
        self.trays: dict[int, list[int | None]] = {1: [], 2: []}
        self.closed = False

    def route(self, tray: int) -> Routed:
        check_tray(tray)
        outcome = self.outcomes.pop(0) if self.outcomes else tray
        sheet = self.compartment.sheet
        if outcome == ROUTE_JAM or not isinstance(outcome, int):
            self.routes.append((sheet, None))
            return Routed(ROUTE_JAM, None, "Gate jam (simulated)")
        self.routes.append((sheet, outcome))
        self.trays[outcome].append(sheet)
        if not self.stuck:
            self.compartment.sheet = None
        return Routed(ROUTE_OK, outcome)

    def take_out(self) -> None:
        """The operator removes the sheet by hand (after a stop)."""
        self.compartment.sheet = None

    def sheet_cleared(self) -> bool:
        return self.compartment.sheet is None

    def close(self) -> None:
        self.closed = True
