"""Feeder + gate of the intake station: interfaces (base.py) and implementations.

    feeder, gate = make_hardware("gpio")     # fake | manual | gpio (ADGVS_INTAKE_HARDWARE)
"""

from collections.abc import Callable

from scan_agent.config import HARDWARE_KINDS, FeederSettings, GateSettings
from scan_agent.hardware.base import (
    FEED_EMPTY,
    FEED_FED,
    FEED_JAM,
    ROUTE_JAM,
    ROUTE_OK,
    TRAY_NAMES,
    TRAYS,
    Fed,
    Feeder,
    Gate,
    Routed,
)
from scan_agent.hardware.fake import Compartment, FakeFeeder, FakeGate
from scan_agent.hardware.gpio import HardwareError, build_gpio
from scan_agent.hardware.manual import ManualFeeder, ManualGate, OperatorConsole


def make_hardware(kind: str, *, feeder_kind: str | None = None, gate_kind: str | None = None,
                  feeder: FeederSettings | None = None, gate: GateSettings | None = None,
                  sheets: int = 0, prompt: Callable[[str], str] = input,
                  say: Callable[[str], None] = print, gpiozero=None) -> tuple[Feeder, Gate]:
    """Feeder + gate of `kind`; `feeder_kind` / `gate_kind` override one side for bring-up
    (manual + gpio). Fake parts only pair with each other (they share a pretend
    compartment)."""
    feeder_kind, gate_kind = feeder_kind or kind, gate_kind or kind
    for value in (feeder_kind, gate_kind):
        if value not in HARDWARE_KINDS:
            raise ValueError(f"Unknown hardware {value!r}: fake, manual or gpio")
    if "fake" in (feeder_kind, gate_kind):
        if feeder_kind != gate_kind:
            raise ValueError("fake hardware cannot be mixed with manual or gpio")
        compartment = Compartment()
        return FakeFeeder(sheets, compartment=compartment), FakeGate(compartment)
    sensor = gpio_feeder = gpio_gate = None
    if "gpio" in (feeder_kind, gate_kind):
        gpio_feeder, gpio_gate, sensor = build_gpio(
            feeder or FeederSettings.from_env(), gate or GateSettings.from_env(),
            gpiozero=gpiozero, use_feeder=feeder_kind == "gpio", use_gate=gate_kind == "gpio")
    console = OperatorConsole(prompt, say)
    return (gpio_feeder or ManualFeeder(console, sensor=sensor),
            gpio_gate or ManualGate(console))


__all__ = ["FEED_EMPTY", "FEED_FED", "FEED_JAM", "ROUTE_JAM", "ROUTE_OK", "TRAYS", "TRAY_NAMES",
           "Compartment", "FakeFeeder", "FakeGate", "Fed", "Feeder", "Gate", "HardwareError",
           "ManualFeeder", "ManualGate", "OperatorConsole", "Routed", "make_hardware"]
