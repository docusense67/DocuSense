"""Manual mode for bring-up: the operator is the feeder and the gate.

    python -m scan_agent intake --hardware manual --source picamera2 --once

No motor and no servo move. For each sheet the terminal asks the operator to place one
sheet in the compartment (Enter; `done` ends the batch), the camera captures it, and once
the backend has decided, the terminal says which tray to put it in and waits until the
operator has taken it out (Enter). This checks the camera, screening, upload and the
backend's decisions on the real station before the feeder and gate are wired.

Bring-up steps mix them with GPIO parts: `--feeder manual --gate gpio` (the gate first),
then `--hardware gpio`; see docs/hardware/README.md.
"""

import logging
import time
from collections.abc import Callable

from scan_agent.hardware.base import (
    FEED_EMPTY,
    FEED_FED,
    FEED_JAM,
    ROUTE_OK,
    TRAY_NAMES,
    Fed,
    Feeder,
    Gate,
    Routed,
    check_tray,
)

log = logging.getLogger("scan_agent.hardware")

END_WORDS = {"done", "d", "end", "q", "quit", "stop"}


class OperatorConsole:
    """Prompts on the terminal (injectable for tests) and the shared compartment state."""

    def __init__(self, prompt: Callable[[str], str] = input,
                 say: Callable[[str], None] = print):
        self.prompt = prompt
        self.say = say
        self.sheet = False  # a sheet is in the compartment (as far as the operator said)
        self.count = 0

    def ask(self, text: str) -> str | None:
        """The operator's answer, None when there is no terminal (EOF)."""
        log.info("Operator: %s", text)
        try:
            return self.prompt(text + " ").strip().lower()
        except EOFError:
            return None


class ManualFeeder(Feeder):
    """The operator places each sheet. With the GPIO gate (`--feeder manual --gate gpio`)
    the compartment sensor confirms the sheet is there, which also checks that sensor."""

    name = "manual"

    def __init__(self, console: OperatorConsole, sensor=None, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, sensor_wait: float = 2.0):
        self.console = console
        self.sensor = sensor  # the compartment sensor, or None
        self.clock = clock
        self.sleep = sleep
        self.sensor_wait = sensor_wait

    def feed_one(self) -> Fed:
        n = self.console.count + 1
        answer = self.console.ask(f"Place sheet {n} face up in the compartment and press "
                                  "Enter (type 'done' to end the batch):")
        if answer is None or answer in END_WORDS:
            return Fed(FEED_EMPTY, "Operator ended the batch")
        if self.sensor is not None:
            deadline = self.clock() + self.sensor_wait
            while not self.sensor.is_active:
                if self.clock() >= deadline:
                    return Fed(FEED_JAM, "The compartment sensor does not see the sheet: check "
                                         "its position and wiring (ADGVS_FEEDER_SHEET_PIN)")
                self.sleep(0.05)
        self.console.count = n
        self.console.sheet = True
        return Fed(FEED_FED)

    def sheet_present(self) -> bool:
        if self.sensor is not None:
            return bool(self.sensor.is_active)
        return self.console.sheet


class ManualGate(Gate):
    """The operator puts the sheet in the tray the backend chose, then confirms it is out."""

    name = "manual"

    def __init__(self, console: OperatorConsole):
        self.console = console
        self.tray: int | None = None
        self.routed = 0

    def route(self, tray: int) -> Routed:
        self.tray = check_tray(tray)
        self.routed += 1
        self.console.sheet = True  # also when a GPIO feeder brought it
        message = f">>> Sheet {self.routed}: put it in {TRAY_NAMES[tray].upper()} <<<"
        log.info(message)
        self.console.say(message)
        return Routed(ROUTE_OK, tray)

    def sheet_cleared(self) -> bool:
        if not self.console.sheet:
            return True
        where = f" and put it in Tray {self.tray}" if self.tray else ""
        self.console.ask(f"Take the sheet out of the compartment{where}, then press Enter:")
        self.console.sheet = False
        self.tray = None
        return True
