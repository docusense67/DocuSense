"""GPIO feeder and gate (gpiozero) for the intake station on the Raspberry Pi 5.

    feeder, gate = build_gpio(FeederSettings.from_env(), GateSettings.from_env())

Wiring is not fixed yet: every pin, angle and timeout comes from ADGVS_FEEDER_* and
ADGVS_GATE_* (scan_agent/config.py, docs/hardware/README.md). gpiozero comes from apt on
the Pi (python3-gpiozero + python3-lgpio, the Pi 5 pin driver), not pip: the venv needs
--system-site-packages (pi-setup.sh --intake). It is imported only when gpio hardware is
used, so the dev PC and CI never need it.

Devices (gpiozero classes):
- feed motor: OutputDevice on one pin (relay / MOSFET), or Motor on an H-bridge
  (forward + backward pins, optional PWM enable);
- compartment sensor (sheet present), optional stack sensor (paper on the feeder) and
  optional exit sensor (sheet on its way to a tray): DigitalInputDevice, pull-up by default
  (the sensor pulls the pin low when it sees paper);
- gate: AngularServo with the angle per tray; optional eject motor (OutputDevice).

The compartment sensor is shared: the feeder stops when it sees the sheet, the gate waits
until it is clear. Motors are always switched off in a `finally`, whatever happens.
"""

import importlib
import logging
import time
from collections.abc import Callable

from scan_agent.config import FeederSettings, GateSettings, parse_pin
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

log = logging.getLogger("scan_agent.hardware")

POLL_S = 0.01  # sensor polling interval


class HardwareError(Exception):
    """The GPIO hardware cannot be used. The message is operator-facing."""


def load_gpiozero():
    try:
        return importlib.import_module("gpiozero")
    except ImportError:
        raise HardwareError(
            "gpiozero missing: sudo apt install python3-gpiozero python3-lgpio and create the "
            "venv with --system-site-packages (sudo pi-setup.sh --intake)") from None


def _pin(raw: str):
    return parse_pin(raw)


def _input(gz, raw: str, pull_up: bool):
    pin = _pin(raw)
    return None if pin is None else gz.DigitalInputDevice(pin, pull_up=pull_up)


class _Waiter:
    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], None]):
        self.clock = clock
        self.sleep = sleep

    def until(self, condition: Callable[[], bool], timeout: float) -> bool:
        deadline = self.clock() + timeout
        while True:
            if condition():
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(POLL_S)


class GpioFeeder(Feeder):
    name = "gpio"

    def __init__(self, settings: FeederSettings, gz, sheet_sensor, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.sheet = sheet_sensor
        self.wait = _Waiter(clock, sleep)
        self.devices: list = []
        try:
            if settings.motor_backward_pin.strip():
                self.motor = gz.Motor(_pin(settings.motor_pin),
                                      _pin(settings.motor_backward_pin),
                                      enable=_pin(settings.motor_enable_pin), pwm=True)
                self.h_bridge = True
            else:
                self.motor = gz.OutputDevice(_pin(settings.motor_pin),
                                             active_high=settings.motor_active_high,
                                             initial_value=False)
                self.h_bridge = False
            self.devices.append(self.motor)
            self.stack = _input(gz, settings.stack_pin, settings.sensor_pull_up)
            if self.stack is not None:
                self.devices.append(self.stack)
        except Exception:
            self.close()
            raise

    def _motor(self, on: bool) -> None:
        if self.h_bridge:
            if on:
                self.motor.forward(self.settings.motor_speed)
            else:
                self.motor.stop()
        elif on:
            self.motor.on()
        else:
            self.motor.off()

    def paper_loaded(self) -> bool | None:
        return None if self.stack is None else bool(self.stack.is_active)

    def sheet_present(self) -> bool:
        return bool(self.sheet.is_active)

    def feed_one(self) -> Fed:
        s = self.settings
        if self.sheet_present():
            return Fed(FEED_JAM, "A sheet is already in the compartment: take it out")
        if self.stack is not None and not self.stack.is_active:
            return Fed(FEED_EMPTY, "Feeder empty")
        self._motor(True)
        try:
            arrived = self.wait.until(self.sheet_present, s.feed_timeout)
            if arrived and s.overrun > 0:
                self.wait.sleep(s.overrun)
        finally:
            self._motor(False)
        if arrived:
            return Fed(FEED_FED)
        if self.stack is None:
            # Without a stack sensor an empty feeder and a misfeed look the same.
            return Fed(FEED_EMPTY, f"No sheet reached the compartment within "
                                   f"{s.feed_timeout:g} s: feeder empty")
        return Fed(FEED_JAM, f"Paper on the feeder but no sheet reached the compartment "
                             f"within {s.feed_timeout:g} s: clear the feeder")

    def describe(self) -> str:
        s = self.settings
        motor = (f"H-bridge {_pin(s.motor_pin)}/{_pin(s.motor_backward_pin)}" if self.h_bridge
                 else f"motor {_pin(s.motor_pin)}")
        return (f"gpio feeder: {motor}, sheet sensor {_pin(s.sheet_pin)}, stack sensor "
                f"{_pin(s.stack_pin) or 'none'}, timeout {s.feed_timeout:g} s")

    def close(self) -> None:
        for device in getattr(self, "devices", []):
            try:
                device.close()
            except Exception:  # pragma: no cover - best effort on shutdown
                pass
        self.devices = []


class GpioGate(Gate):
    name = "gpio"

    def __init__(self, settings: GateSettings, gz, sheet_sensor, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.sheet = sheet_sensor
        self.wait = _Waiter(clock, sleep)
        self.clock = clock
        self.devices: list = []
        s = settings
        try:
            self.servo = gz.AngularServo(
                _pin(s.servo_pin), initial_angle=s.home_angle, min_angle=s.min_angle,
                max_angle=s.max_angle, min_pulse_width=s.min_pulse_ms / 1000,
                max_pulse_width=s.max_pulse_ms / 1000)
            self.devices.append(self.servo)
            pin = _pin(s.eject_pin)
            self.eject = (None if pin is None else
                          gz.OutputDevice(pin, active_high=s.eject_active_high,
                                          initial_value=False))
            if self.eject is not None:
                self.devices.append(self.eject)
            self.exit = _input(gz, s.exit_pin, s.sensor_pull_up)
            if self.exit is not None:
                self.devices.append(self.exit)
        except Exception:
            self.close()
            raise
        self._move(s.home_angle)

    def _move(self, angle: float) -> None:
        self.servo.angle = angle
        self.wait.sleep(self.settings.move_time)
        if self.settings.detach:
            self.servo.detach()

    def sheet_cleared(self) -> bool:
        return not self.sheet.is_active and (self.exit is None or not self.exit.is_active)

    def route(self, tray: int) -> Routed:
        s = self.settings
        check_tray(tray)
        self.servo.angle = s.angle(tray)
        self.wait.sleep(s.move_time)
        seen_exit = self.exit is None
        start = self.clock()
        if self.eject is not None:
            self.eject.on()
        try:
            def done() -> bool:
                nonlocal seen_exit
                if self.exit is not None and self.exit.is_active:
                    seen_exit = True
                left = not self.sheet.is_active
                if self.eject is not None and (left or self.clock() - start >= s.eject_time):
                    self.eject.off()
                return left and seen_exit and self.sheet_cleared()

            ok = self.wait.until(done, s.clear_timeout)
        finally:
            if self.eject is not None:
                self.eject.off()
        if not ok:
            if s.detach:
                self.servo.detach()  # stay where it is: the operator clears the jam
            if self.sheet.is_active:
                detail = "Sheet did not leave the compartment"
            elif self.exit is not None and self.exit.is_active:
                detail = "Sheet stuck at the exit sensor"
            else:
                detail = "Sheet left the compartment but never passed the exit sensor"
            return Routed(ROUTE_JAM, None, f"Gate jam (Tray {tray}): {detail}")
        self._move(s.home_angle)
        return Routed(ROUTE_OK, tray)

    def describe(self) -> str:
        s = self.settings
        return (f"gpio gate: servo {_pin(s.servo_pin)} (Tray 1 {s.tray1_angle:g} deg, Tray 2 "
                f"{s.tray2_angle:g} deg, home {s.home_angle:g} deg, pulses "
                f"{s.min_pulse_ms:g}-{s.max_pulse_ms:g} ms), eject {_pin(s.eject_pin) or 'none'},"
                f" exit sensor {_pin(s.exit_pin) or 'none'}")

    def close(self) -> None:
        if getattr(self, "eject", None) is not None:
            try:
                self.eject.off()
            except Exception:  # pragma: no cover
                pass
        for device in getattr(self, "devices", []):
            try:
                device.close()
            except Exception:  # pragma: no cover - best effort on shutdown
                pass
        self.devices = []


class SharedSensor:
    """The compartment sensor, used by both the feeder and the gate, closed once."""

    def __init__(self, device):
        self.device = device

    @property
    def is_active(self) -> bool:
        return bool(self.device.is_active)

    def close(self) -> None:
        self.device.close()


def build_gpio(feeder: FeederSettings, gate: GateSettings, *, gpiozero=None,
               use_feeder: bool = True, use_gate: bool = True,
               clock: Callable[[], float] = time.monotonic,
               sleep: Callable[[float], None] = time.sleep
               ) -> tuple[GpioFeeder | None, GpioGate | None, SharedSensor]:
    """The GPIO feeder and/or gate and the compartment sensor they share (settings already
    validated by config.validate_intake). Bring-up can use one of them with a manual
    counterpart (`intake --feeder manual --gate gpio`)."""
    gz = gpiozero or load_gpiozero()
    opened: list = []
    f = g = None
    try:
        sensor = SharedSensor(gz.DigitalInputDevice(_pin(feeder.sheet_pin),
                                                    pull_up=feeder.sensor_pull_up))
        opened.append(sensor)
        if use_feeder:
            f = GpioFeeder(feeder, gz, sensor, clock=clock, sleep=sleep)
            opened.append(f)
        if use_gate:
            g = GpioGate(gate, gz, sensor, clock=clock, sleep=sleep)
    except Exception as e:
        for item in reversed(opened):
            item.close()
        raise HardwareError(f"GPIO setup failed ({type(e).__name__}: {e}): check the pins "
                            "in /etc/adgvs/station.env and that this is the Raspberry Pi") from e
    owner = f or g
    if owner is not None:
        owner.devices.append(sensor)  # closed with its owner
    return f, g, sensor
