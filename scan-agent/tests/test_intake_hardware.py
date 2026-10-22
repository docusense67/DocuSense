"""Intake station hardware: settings validation, fake/manual feeder + gate, and the gpiozero
implementation against a fake `gpiozero` module (a small paper-path simulation)."""

import sys
import types

import pytest

from scan_agent.config import (
    ConfigError,
    FeederSettings,
    GateSettings,
    IntakeSettings,
    parse_pin,
    validate_intake,
)
from scan_agent.hardware import (
    FEED_EMPTY,
    FEED_FED,
    FEED_JAM,
    ROUTE_JAM,
    ROUTE_OK,
    Compartment,
    FakeFeeder,
    FakeGate,
    HardwareError,
    ManualFeeder,
    ManualGate,
    OperatorConsole,
    make_hardware,
)
from scan_agent.hardware.gpio import build_gpio

# ---- settings ----------------------------------------------------------------------------


@pytest.mark.parametrize("raw, pin", [("17", "GPIO17"), (" gpio4 ", "GPIO4"), ("BCM27", "GPIO27"),
                                      ("BOARD11", "BOARD11"), ("", None), ("0", "GPIO0")])
def test_pins_are_named_as_gpiozero_names_them(raw, pin):
    assert parse_pin(raw) == pin


@pytest.mark.parametrize("raw", ["28", "GPIO40", "BOARD41", "BOARD0", "pin7", "17.5", "-1"])
def test_pins_outside_the_header_are_refused(raw):
    with pytest.raises(ValueError, match="not a GPIO pin"):
        parse_pin(raw)


GPIO_ENV = {"ADGVS_INTAKE_HARDWARE": "gpio", "ADGVS_FEEDER_MOTOR_PIN": "17",
            "ADGVS_FEEDER_SHEET_PIN": "27", "ADGVS_GATE_SERVO_PIN": "18"}


def settings(env):
    return (IntakeSettings.from_env(env), FeederSettings.from_env(env),
            GateSettings.from_env(env))


def test_minimal_gpio_settings_are_valid():
    intake, feeder, gate = settings(GPIO_ENV)
    validate_intake(intake, feeder, gate)
    assert intake.hardware == "gpio" and gate.angle(1) == -45.0 and gate.angle(2) == 45.0


def test_settings_problems_are_all_listed():
    env = {"ADGVS_INTAKE_HARDWARE": "gpio", "ADGVS_FEEDER_MOTOR_PIN": "17",
           "ADGVS_GATE_SERVO_PIN": "GPIO17", "ADGVS_GATE_EXIT_PIN": "99",
           "ADGVS_GATE_TRAY1_ANGLE": "120", "ADGVS_GATE_TRAY2_ANGLE": "121",
           "ADGVS_GATE_MIN_PULSE_MS": "2.5", "ADGVS_GATE_MAX_PULSE_MS": "1.0",
           "ADGVS_FEEDER_MOTOR_ENABLE_PIN": "22", "ADGVS_FEEDER_FEED_TIMEOUT": "0",
           "ADGVS_INTAKE_HOLD_TIMEOUT": "-1"}
    with pytest.raises(ConfigError) as e:
        validate_intake(*settings(env))
    text = str(e.value)
    for expected in ("ADGVS_FEEDER_SHEET_PIN is required",
                     "ADGVS_GATE_SERVO_PIN: GPIO17 is already used by ADGVS_FEEDER_MOTOR_PIN",
                     "ADGVS_GATE_EXIT_PIN: '99' is not a GPIO pin",
                     "ADGVS_GATE_TRAY1_ANGLE=120.0 is outside", "must differ by at least 5",
                     "servo pulses", "ADGVS_FEEDER_MOTOR_ENABLE_PIN needs",
                     "ADGVS_FEEDER_FEED_TIMEOUT must be > 0",
                     "ADGVS_INTAKE_HOLD_TIMEOUT must be > 0"):
        assert expected in text
    assert len(e.value.problems) == 10  # tray2 121 is outside too


def test_manual_and_fake_need_no_pins():
    validate_intake(IntakeSettings(hardware="manual"))
    validate_intake(IntakeSettings(hardware="fake"))
    with pytest.raises(ConfigError, match="one of fake, manual, gpio"):
        validate_intake(IntakeSettings(hardware="servo"))


def test_boolean_and_number_settings_from_env():
    gate = GateSettings.from_env({"ADGVS_GATE_DETACH": "no", "ADGVS_GATE_SENSOR_PULL_UP": "0",
                                  "ADGVS_GATE_MOVE_TIME": "0.8"})
    assert gate.detach is False and gate.sensor_pull_up is False and gate.move_time == 0.8
    with pytest.raises(ValueError, match="ADGVS_GATE_DETACH='maybe' is not a valid bool"):
        GateSettings.from_env({"ADGVS_GATE_DETACH": "maybe"})
    with pytest.raises(ValueError, match="ADGVS_INTAKE_HOLD_TIMEOUT"):
        IntakeSettings.from_env({"ADGVS_INTAKE_HOLD_TIMEOUT": "two minutes"})


# ---- fake and manual ---------------------------------------------------------------------


def test_fake_feeder_and_gate_share_the_compartment():
    compartment = Compartment()
    feeder, gate = FakeFeeder(2, compartment=compartment), FakeGate(compartment, ["jam"])
    assert feeder.feed_one().result == FEED_FED and feeder.sheet_present()
    assert feeder.feed_one().result == FEED_JAM  # not cleared yet
    assert gate.route(1).result == ROUTE_JAM and not gate.sheet_cleared()
    gate.take_out()
    assert feeder.feed_one().result == FEED_FED
    routed = gate.route(2)
    assert routed.result == ROUTE_OK and routed.tray == 2 and gate.trays == {1: [], 2: [2]}
    assert feeder.feed_one().result == FEED_EMPTY
    with pytest.raises(ValueError):
        gate.route(3)


def test_manual_mode_asks_the_operator():
    answers = iter(["", "", "done"])
    said, asked = [], []
    console = OperatorConsole(lambda text: asked.append(text) or next(answers), said.append)
    feeder, gate = ManualFeeder(console), ManualGate(console)

    assert feeder.feed_one().result == FEED_FED and feeder.sheet_present()
    assert "Place sheet 1" in asked[0]
    assert gate.route(2).tray == 2
    assert said == [">>> Sheet 1: put it in TRAY 2 (NEEDS MANUAL REVIEW) <<<"]
    assert gate.sheet_cleared() and not feeder.sheet_present()
    assert "put it in Tray 2" in asked[1]
    assert feeder.feed_one().result == FEED_EMPTY  # "done"


def test_manual_mode_without_a_terminal_ends_the_batch():
    def eof(text):
        raise EOFError

    console = OperatorConsole(eof, lambda text: None)
    assert ManualFeeder(console).feed_one().result == FEED_EMPTY


# ---- GPIO (fake gpiozero) ----------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 100.0
        self.world = None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.world is not None:
            self.world.step(seconds)


class World:
    """The paper path: a stack, the compartment (sheet sensor), the gate and the trays."""

    def __init__(self, gate: GateSettings, *, stack=3, feed_time=0.8, leave_time=0.4,
                 feed_jams=False, gate_jams=False, eject=False):
        self.gate = gate
        self.stack = stack
        self.feed_time, self.leave_time = feed_time, leave_time
        self.feed_jams, self.gate_jams, self.eject = feed_jams, gate_jams, eject
        self.sheet = False
        self.exit = False
        self.trays: dict[int, int] = {1: 0, 2: 0}
        self.progress = 0.0
        self.leaving = 0.0
        self.motor = False
        self.eject_on = False
        self.angle: float | None = None
        self.exit_until = None
        self.time = 0.0
        self.roles: dict[str, str] = {}  # pin -> sheet | stack | exit | eject

    def tray_at(self):
        for tray in (1, 2):
            if self.angle is not None and abs(self.angle - self.gate.angle(tray)) < 1e-6:
                return tray
        return None

    def step(self, dt):
        self.time += dt
        if self.motor and self.stack and not self.sheet and not self.feed_jams:
            self.progress += dt
            if self.progress >= self.feed_time:
                self.sheet, self.stack, self.progress = True, self.stack - 1, 0.0
        tray = self.tray_at()
        if (self.sheet and tray and not self.gate_jams
                and (self.eject_on or not self.eject)):
            self.leaving += dt
            if self.leaving >= self.leave_time:
                self.sheet, self.leaving = False, 0.0
                self.trays[tray] += 1
                self.exit, self.exit_until = True, self.time + 0.1
        if self.exit and self.time >= self.exit_until:
            self.exit = False


def fake_gpiozero(world: World, log: list):
    gz = types.ModuleType("gpiozero")
    pins = {}

    def claim(pin, device):
        if pin in pins:
            raise RuntimeError(f"pin {pin} in use")  # gpiozero's GPIOPinInUse
        pins[pin] = device

    class Device:
        def __init__(self, pin):
            self.pin = pin
            self.closed = False
            claim(pin, self)

        def close(self):
            self.closed = True
            pins.pop(self.pin, None)
            log.append(("close", self.pin))

    class DigitalInputDevice(Device):
        def __init__(self, pin, pull_up=False):
            super().__init__(pin)
            self.pull_up = pull_up
            self.role = None

        @property
        def is_active(self):
            return {"sheet": world.sheet, "stack": world.stack > 0,
                    "exit": world.exit}[world.roles[self.pin]]

    class OutputDevice(Device):
        def __init__(self, pin, active_high=True, initial_value=False):
            super().__init__(pin)
            self.active_high = active_high
            self.value = bool(initial_value)

        def on(self):
            self.value = True
            self._apply()

        def off(self):
            self.value = False
            self._apply()

        def _apply(self):
            log.append((self.pin, self.value))
            if world.roles.get(self.pin) == "eject":
                world.eject_on = self.value
            else:
                world.motor = self.value

    class Motor:
        def __init__(self, forward, backward, enable=None, pwm=True):
            self.pins = [p for p in (forward, backward, enable) if p]
            for pin in self.pins:
                claim(pin, self)
            self.speed = 0.0

        def forward(self, speed=1):
            self.speed = speed
            world.motor = True
            log.append(("forward", speed))

        def stop(self):
            self.speed = 0.0
            world.motor = False
            log.append(("stop",))

        def close(self):
            for pin in self.pins:
                pins.pop(pin, None)
            log.append(("close", "motor"))

    class AngularServo(Device):
        def __init__(self, pin, initial_angle=0.0, min_angle=-90, max_angle=90,
                     min_pulse_width=1 / 1000, max_pulse_width=2 / 1000):
            super().__init__(pin)
            self.range = (min_angle, max_angle, min_pulse_width, max_pulse_width)
            self._angle = None
            self.angle = initial_angle

        @property
        def angle(self):
            return self._angle

        @angle.setter
        def angle(self, value):
            self._angle = value
            world.angle = value
            log.append(("angle", value))

        def detach(self):
            log.append(("detach",))

    gz.DigitalInputDevice = DigitalInputDevice
    gz.OutputDevice = OutputDevice
    gz.Motor = Motor
    gz.AngularServo = AngularServo
    gz.pins = pins
    return gz


def gpio_station(*, feeder_env=None, gate_env=None, **world_kwargs):
    env = {**GPIO_ENV, **(feeder_env or {}), **(gate_env or {})}
    _, feeder_settings, gate_settings = settings(env)
    world = World(gate_settings, **world_kwargs)
    world.roles = {parse_pin(feeder_settings.sheet_pin): "sheet"}
    for raw, role in ((feeder_settings.stack_pin, "stack"), (gate_settings.exit_pin, "exit"),
                      (gate_settings.eject_pin, "eject")):
        if raw:
            world.roles[parse_pin(raw)] = role
    log: list = []
    gz = fake_gpiozero(world, log)
    clock = Clock()
    clock.world = world
    feeder, gate, _ = build_gpio(feeder_settings, gate_settings, gpiozero=gz, clock=clock,
                                 sleep=clock.sleep)
    return types.SimpleNamespace(feeder=feeder, gate=gate, world=world, log=log, gz=gz,
                                 clock=clock)


def test_gpio_feeds_one_sheet_and_stops_the_motor():
    hw = gpio_station(feeder_env={"ADGVS_FEEDER_OVERRUN": "0.2"})
    start = hw.clock.now
    assert hw.feeder.feed_one().result == FEED_FED
    assert hw.world.sheet and hw.world.stack == 2 and not hw.world.motor
    assert 1.0 <= hw.clock.now - start < 1.1  # 0.8 s to arrive + 0.2 s overrun
    assert ("GPIO17", True) in hw.log and hw.log[-1] == ("GPIO17", False)
    assert hw.feeder.feed_one().result == FEED_JAM  # compartment not clear: motor stays off
    assert hw.log[-1] == ("GPIO17", False) and hw.log.count(("GPIO17", True)) == 1


def test_gpio_without_stack_sensor_reads_a_timeout_as_empty():
    hw = gpio_station(stack=0)
    fed = hw.feeder.feed_one()
    assert fed.result == FEED_EMPTY and "within 5 s" in fed.detail
    assert not hw.world.motor and hw.feeder.paper_loaded() is None


def test_gpio_stack_sensor_tells_a_misfeed_from_an_empty_feeder():
    hw = gpio_station(feeder_env={"ADGVS_FEEDER_STACK_PIN": "22"}, feed_jams=True)
    assert hw.feeder.paper_loaded() is True
    fed = hw.feeder.feed_one()
    assert fed.result == FEED_JAM and "clear the feeder" in fed.detail
    assert not hw.world.motor

    hw = gpio_station(feeder_env={"ADGVS_FEEDER_STACK_PIN": "22"}, stack=0)
    assert hw.feeder.paper_loaded() is False
    assert hw.feeder.feed_one().result == FEED_EMPTY
    assert not any(entry == ("GPIO17", True) for entry in hw.log)  # motor never started


def test_gpio_h_bridge_feeder_runs_forward_at_the_set_speed():
    hw = gpio_station(feeder_env={"ADGVS_FEEDER_MOTOR_BACKWARD_PIN": "23",
                                  "ADGVS_FEEDER_MOTOR_ENABLE_PIN": "24",
                                  "ADGVS_FEEDER_MOTOR_SPEED": "0.6"})
    assert hw.feeder.feed_one().result == FEED_FED
    assert ("forward", 0.6) in hw.log and hw.log[-1] == ("stop",)


def test_gpio_gate_routes_to_the_tray_angle_and_returns_home():
    hw = gpio_station(gate_env={"ADGVS_GATE_TRAY1_ANGLE": "-60", "ADGVS_GATE_HOME_ANGLE": "5"})
    hw.feeder.feed_one()
    hw.log.clear()
    routed = hw.gate.route(1)
    assert routed.result == ROUTE_OK and routed.tray == 1
    assert hw.world.trays == {1: 1, 2: 0} and hw.gate.sheet_cleared()
    angles = [entry[1] for entry in hw.log if entry[0] == "angle"]
    assert angles == [-60.0, 5.0] and hw.log[-1] == ("detach",)

    hw.feeder.feed_one()
    assert hw.gate.route(2).tray == 2 and hw.world.trays == {1: 1, 2: 1}


def test_gpio_gate_with_eject_and_exit_sensor():
    hw = gpio_station(gate_env={"ADGVS_GATE_EJECT_PIN": "5", "ADGVS_GATE_EXIT_PIN": "6"},
                      eject=True)
    hw.feeder.feed_one()
    assert hw.gate.route(2).result == ROUTE_OK
    assert ("GPIO5", True) in hw.log and not hw.world.eject_on
    assert hw.world.trays[2] == 1 and not hw.world.exit


def test_gpio_gate_jam_when_the_sheet_does_not_leave():
    hw = gpio_station(gate_env={"ADGVS_GATE_EJECT_PIN": "5", "ADGVS_GATE_CLEAR_TIMEOUT": "2"},
                      eject=True, gate_jams=True)
    hw.feeder.feed_one()
    start = hw.clock.now
    routed = hw.gate.route(1)
    assert routed.result == ROUTE_JAM and routed.tray is None
    assert "did not leave the compartment" in routed.detail
    assert 2.0 <= hw.clock.now - start < 3.0
    assert not hw.world.eject_on and not hw.gate.sheet_cleared()
    assert hw.world.angle == -45.0  # left at the tray: the operator clears it


def test_gpio_devices_share_the_sheet_sensor_and_close():
    hw = gpio_station(feeder_env={"ADGVS_FEEDER_STACK_PIN": "22"},
                      gate_env={"ADGVS_GATE_EXIT_PIN": "6"})
    assert set(hw.gz.pins) == {"GPIO17", "GPIO27", "GPIO22", "GPIO18", "GPIO6"}
    hw.feeder.close()
    hw.gate.close()
    assert hw.gz.pins == {}


def test_gpio_setup_failure_releases_pins_and_explains():
    env = {**GPIO_ENV}
    _, feeder_settings, gate_settings = settings(env)
    world = World(gate_settings)
    world.roles = {"GPIO27": "sheet"}
    gz = fake_gpiozero(world, [])

    class Broken:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("no PWM on this pin")

    gz.AngularServo = Broken
    with pytest.raises(HardwareError, match="GPIO setup failed.*no PWM"):
        build_gpio(feeder_settings, gate_settings, gpiozero=gz)
    assert gz.pins == {}


def test_make_hardware_imports_gpiozero_lazily(monkeypatch):
    _, feeder_settings, gate_settings = settings(GPIO_ENV)
    world = World(gate_settings)
    world.roles = {"GPIO27": "sheet"}
    monkeypatch.setitem(sys.modules, "gpiozero", fake_gpiozero(world, []))
    feeder, gate = make_hardware("gpio", feeder=feeder_settings, gate=gate_settings)
    assert "servo GPIO18" in gate.describe() and "motor GPIO17" in feeder.describe()

    monkeypatch.setitem(sys.modules, "gpiozero", None)  # not installed
    with pytest.raises(HardwareError, match="apt install python3-gpiozero"):
        make_hardware("gpio", feeder=feeder_settings, gate=gate_settings)


# ---- bring-up: manual and GPIO parts mixed -----------------------------------------------


def test_gate_bring_up_needs_only_the_gate_and_sheet_sensor_pins():
    env = {"ADGVS_FEEDER_SHEET_PIN": "27", "ADGVS_GATE_SERVO_PIN": "18"}
    intake, feeder, gate = settings(env)
    validate_intake(intake, feeder, gate, feeder_kind="manual", gate_kind="gpio")
    with pytest.raises(ConfigError, match="ADGVS_FEEDER_MOTOR_PIN is required"):
        validate_intake(intake, feeder, gate, feeder_kind="gpio", gate_kind="gpio")
    with pytest.raises(ConfigError, match="cannot be mixed"):
        validate_intake(intake, feeder, gate, feeder_kind="fake", gate_kind="gpio")
    with pytest.raises(ValueError, match="cannot be mixed"):
        make_hardware("manual", feeder_kind="fake")


def test_manual_feeder_with_the_gpio_gate_checks_the_sheet_sensor():
    _, feeder_settings, gate_settings = settings({**GPIO_ENV})
    world = World(gate_settings)
    world.roles = {"GPIO27": "sheet"}
    gz = fake_gpiozero(world, [])
    answers = iter(["", ""])

    def operator(text):  # the operator puts a sheet in, but only the first time
        answer = next(answers)
        world.sheet = world.sheet or "sheet 1" in text.lower()
        return answer

    feeder, gate = make_hardware("gpio", feeder_kind="manual", feeder=feeder_settings,
                                 gate=gate_settings, gpiozero=gz, prompt=operator,
                                 say=lambda text: None)
    assert isinstance(feeder, ManualFeeder) and gate.name == "gpio"
    assert set(gz.pins) == {"GPIO27", "GPIO18"}  # no motor pin claimed
    clock = Clock()
    clock.world = world
    feeder.clock, feeder.sleep = clock, clock.sleep
    gate.wait.clock = gate.clock = clock
    gate.wait.sleep = clock.sleep
    assert feeder.feed_one().result == FEED_FED and feeder.sheet_present()
    assert gate.route(1).result == ROUTE_OK and world.trays[1] == 1
    jam = feeder.feed_one()  # Enter pressed, but the sensor sees nothing
    assert jam.result == FEED_JAM and "ADGVS_FEEDER_SHEET_PIN" in jam.detail


def test_gpio_feeder_with_the_manual_gate_asks_for_the_sheet_to_be_taken_out():
    _, feeder_settings, gate_settings = settings({**GPIO_ENV})
    world = World(gate_settings)
    world.roles = {"GPIO27": "sheet"}
    asked, said = [], []
    feeder, gate = make_hardware("gpio", gate_kind="manual", feeder=feeder_settings,
                                 gate=gate_settings, gpiozero=fake_gpiozero(world, []),
                                 prompt=lambda text: asked.append(text) or "",
                                 say=said.append)
    assert feeder.name == "gpio" and isinstance(gate, ManualGate)
    assert gate.route(2).tray == 2 and said == [
        ">>> Sheet 1: put it in TRAY 2 (NEEDS MANUAL REVIEW) <<<"]
    assert gate.sheet_cleared() and "Take the sheet out" in asked[0]
