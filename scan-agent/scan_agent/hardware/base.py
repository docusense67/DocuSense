"""Feeder and gate interfaces of the intake station (camera compartment + two trays).

    feeder.feed_one()   -> Fed(FEED_FED | FEED_EMPTY | FEED_JAM, detail)
    feeder.sheet_present()   is a sheet in the compartment?
    gate.route(tray)    -> Routed(ROUTE_OK | ROUTE_JAM, tray, detail)
    gate.sheet_cleared()     has the sheet left the compartment?

The hardware is not fixed yet: the intake cycle (scan_agent/intake.py) only sees
these interfaces. Implementations: fake.py (tests), manual.py (bring-up: the operator is
the feeder and the gate), gpio.py (gpiozero, pins from ADGVS_FEEDER_* / ADGVS_GATE_*).

The gate only ever moves on the backend's decision; nothing here decides a tray.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

FEED_FED = "fed"  # a sheet is in the compartment
FEED_EMPTY = "empty"  # no paper left: the batch is complete
FEED_JAM = "jam"  # a sheet started but did not arrive, or the compartment was not clear

ROUTE_OK = "ok"
ROUTE_JAM = "jam"

TRAYS = (1, 2)  # 1 = passed screening, 2 = manual review
TRAY_NAMES = {1: "Tray 1 (passed screening)", 2: "Tray 2 (needs manual review)"}


@dataclass(frozen=True)
class Fed:
    result: str  # FEED_FED | FEED_EMPTY | FEED_JAM
    detail: str = ""  # operator-facing, no personal data


@dataclass(frozen=True)
class Routed:
    result: str  # ROUTE_OK | ROUTE_JAM
    tray: int | None = None  # the tray the sheet went to (None on a jam)
    detail: str = ""


class Feeder(ABC):
    name = ""

    @abstractmethod
    def feed_one(self) -> Fed:
        """Bring exactly one sheet into the compartment."""

    @abstractmethod
    def sheet_present(self) -> bool:
        """A sheet is in the compartment."""

    def paper_loaded(self) -> bool | None:
        """Paper on the input stack: True/False, None when there is no stack sensor (the
        service then simply tries to feed)."""
        return None

    def describe(self) -> str:
        return self.name

    def close(self) -> None:  # noqa: B027 - optional hook
        """Stop motors and release pins."""


class Gate(ABC):
    name = ""

    @abstractmethod
    def route(self, tray: int) -> Routed:
        """Send the sheet in the compartment to `tray` (1 or 2), as the backend decided."""

    @abstractmethod
    def sheet_cleared(self) -> bool:
        """The compartment is empty again (the next sheet may be fed)."""

    def describe(self) -> str:
        return self.name

    def close(self) -> None:  # noqa: B027 - optional hook
        """Return to the holding position and release pins."""


def check_tray(tray: int) -> int:
    if tray not in TRAYS:
        raise ValueError(f"tray must be 1 or 2, got {tray!r}")
    return tray
