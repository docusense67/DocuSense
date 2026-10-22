import os
import shutil
from dataclasses import dataclass, fields
from pathlib import Path

DPI = 300
A4_MM = (210.0, 297.0)
MM_TO_PX = DPI / 25.4
A4_PX = (round(A4_MM[0] * MM_TO_PX), round(A4_MM[1] * MM_TO_PX))  # (2480, 3508)

# The system footer is printed at the same place on every page of every template
# (backend app/documents/zones.py SYSTEM_ZONES); splitting needs it before any lookup.
FOOTER_ZONE = {"x": 15.0, "y": 279.0, "w": 180.0, "h": 12.0}

_WINDOWS_DEFAULT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def tesseract_cmd() -> str:
    """TESSERACT_CMD env var, then PATH, then the default Windows install location."""
    if cmd := os.environ.get("TESSERACT_CMD"):
        return cmd
    if found := shutil.which("tesseract"):
        return found
    if _WINDOWS_DEFAULT.exists():
        return str(_WINDOWS_DEFAULT)
    raise RuntimeError("Tesseract not found. Install Tesseract 5 or set TESSERACT_CMD.")


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _boolean(raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(raw)


def _from_env(cls, prefix: str, environ=None):
    """A settings dataclass with each field overridable as <prefix><FIELD NAME> (typed like
    its default). A malformed value fails loudly at start-up rather than being ignored."""
    env = os.environ if environ is None else environ
    values = {}
    for f in fields(cls):
        raw = env.get(prefix + f.name.upper())
        if raw is None or raw.strip() == "":
            continue
        kind = type(f.default)
        try:
            if kind is bool:
                values[f.name] = _boolean(raw)
                continue
            values[f.name] = raw.strip() if kind is str else kind(float(raw) if kind is int
                                                                else raw)
        except ValueError:
            raise ValueError(f"{prefix}{f.name.upper()}={raw!r} is not a valid "
                             f"{kind.__name__}") from None
    return cls(**values)


@dataclass(frozen=True)
class QualityThresholds:
    """Capture QA for camera frames (contract `quality` block, docs/intake/screening-contract.md).

    Each value is overridable as ADGVS_QUALITY_<NAME>, e.g. ADGVS_QUALITY_BLUR_MIN=60. The
    defaults come from the synthetic frames in tests/test_camera_quality.py (numbers in the
    comments); recalibrate them with `python -m scan_agent camera-test` on the real station.
    These only decide whether the agent recaptures and what it reports as `ok`/`issues`;
    every screening decision stays in the backend."""

    # Variance of the Laplacian on the normalized page (A4, 300 DPI, paper levelled).
    # Synthetic photos of the TOR fixture: sharp 350-550 (a sparse page ~190-300); OCR
    # read every field and grade down to ~36 and made its first errors at 30 (Gaussian
    # blur sigma 1.8 px at 300 DPI) and 11 (sigma 1.8 at 200 DPI).
    blur_min: float = 40.0
    # Mean brightness (0..1) of the page as captured, before levelling. A well-lit page is
    # 0.80-0.87. Levelling let OCR read the TOR down to 0.21 with mild synthetic noise, but
    # real sensor noise grows as light falls, so keep a margin.
    brightness_min: float = 0.35
    # Above this the paper itself is clipping (gain x1.08: 0.935 and reads; x1.15: 0.963
    # with 92% of the page clipped).
    brightness_max: float = 0.95
    # Share of clipped highlight pixels (>= glare_level after 4x4 averaging, so a lone hot
    # pixel does not count). 0.5% of A4 is a ~2 cm spot: enough to wipe out a grade or a
    # name. Clean synthetic pages measure 0.0, a 1.4 cm hotspot 0.001-0.004, 2.8 cm 0.010.
    glare_max: float = 0.005
    glare_level: int = 250
    # Page rotation in the frame. The perspective correction undoes any angle, but beyond
    # this the compartment guides did not hold the sheet (misfeed, folded corner); the
    # scanner path's deskew uses the same limit (cleanup.MAX_SKEW_DEG).
    skew_max_deg: float = 10.0
    # A bright region smaller than this share of the frame is not a page (page_not_found).
    coverage_min: float = 0.10
    # Pixels per inch across the detected page. OCR is tuned for 300 DPI. The TOR fixture
    # still read at 140 DPI when sharp, but at 200 DPI it tolerates half the blur it does
    # at 250-300, and intake documents carry smaller print. Camera Module 3 with the page
    # across the frame gives ~280-390 DPI; V2 ~250 across, ~195 upright (flagged).
    dpi_min: int = 200

    @classmethod
    def from_env(cls, environ=None) -> "QualityThresholds":
        return _from_env(cls, "ADGVS_QUALITY_", environ)


# Capture QA policy (agent side, not a screening decision): recapture an unusable frame up
# to this many attempts in total, then submit the best one with quality.ok = false.
MAX_CAPTURE_ATTEMPTS = int(os.environ.get("ADGVS_CAPTURE_MAX_ATTEMPTS", "3"))


@dataclass(frozen=True)
class CameraSettings:
    """Camera capture settings, each overridable as ADGVS_CAMERA_<NAME>."""

    source: str = "picamera2"  # picamera2 | opencv | file
    # picamera2: camera number (default 0); opencv: device index or stream URL; file: folder
    device: str = ""
    # Clockwise rotation (0/90/180/270) that turns the mounted camera's frame upright. The
    # 16:9 Camera Module 3 sees A4 at ~390 DPI when the page lies across the frame (90).
    rotate: int = 0
    resolution: str = ""  # opencv: "WxH" to request (default: the largest the camera gives)
    focus: str = "auto"  # auto (autofocus cycle per capture, if the lens has AF) | dioptres
    exposure: str = "auto"  # auto | lock (meter once, then fixed) | exposure time in us
    gain: float = 0.0  # analogue gain with a fixed exposure time (0 = 1.0)
    awb: str = "auto"  # auto | lock | "red,blue" colour gains
    still_frames: int = 3  # consecutive preview frames that must be still before the shot
    still_diff: float = 2.0  # mean absolute grey-level change below which a frame is still
    change_diff: float = 8.0  # change that marks the next page after one was taken
    timeout: float = 10.0  # seconds to wait for a still page before page_not_found
    preview_width: int = 640
    page_aspect: str = "a4"  # a4: output 2480 x 3508; keep: measured aspect, 2480 wide

    @classmethod
    def from_env(cls, environ=None) -> "CameraSettings":
        return _from_env(cls, "ADGVS_CAMERA_", environ)


# ---- intake station: feeder, gate, cycle (scan_agent/hardware/, scan_agent/intake.py) ----
#
# Pins are not fixed yet: every pin, angle and timeout is a setting. GPIO
# pins are BCM numbers ("17"), or gpiozero names ("GPIO17", "BOARD11" = physical pin 11).
# Settings are checked when the intake starts (validate_intake): a wrong value stops the
# service with a message instead of moving a motor the wrong way.


class ConfigError(ValueError):
    """Invalid station settings. `problems` lists every one, operator-facing."""

    def __init__(self, problems: list[str]):
        super().__init__("Invalid station settings:\n  - " + "\n  - ".join(problems))
        self.problems = problems


HARDWARE_KINDS = ("fake", "manual", "gpio")
BCM_PINS = range(0, 28)  # GPIO0-GPIO27 on the 40-pin header
BOARD_PINS = range(1, 41)


def parse_pin(raw: str) -> str | None:
    """A pin setting as gpiozero names it ("GPIO17" / "BOARD11"), "" -> None. ValueError
    when it is not a header GPIO."""
    value = raw.strip().upper()
    if not value:
        return None
    for prefix, valid in (("BOARD", BOARD_PINS), ("BCM", BCM_PINS), ("GPIO", BCM_PINS),
                          ("", BCM_PINS)):
        if value.startswith(prefix) and value[len(prefix):].isdigit():
            number = int(value[len(prefix):])
            if number not in valid:
                break
            return f"BOARD{number}" if prefix == "BOARD" else f"GPIO{number}"
    raise ValueError(f"{raw!r} is not a GPIO pin (BCM 0-27, e.g. 17 or GPIO17; or BOARD1-40)")


@dataclass(frozen=True)
class FeederSettings:
    """Automatic feeder: one sheet per cycle into the camera compartment. ADGVS_FEEDER_<NAME>.

    The feed motor runs until the compartment sensor sees the sheet (plus `overrun` to seat
    it), at most `feed_timeout` seconds. A DC motor through a relay/MOSFET is one pin
    (gpiozero OutputDevice); an H-bridge (forward + backward, optional PWM enable) is a
    gpiozero Motor."""

    motor_pin: str = ""  # required for gpio: motor on (relay/MOSFET) or H-bridge forward
    motor_backward_pin: str = ""  # H-bridge reverse input: makes it a gpiozero Motor
    motor_enable_pin: str = ""  # H-bridge enable (PWM speed), with motor_backward_pin
    motor_speed: float = 1.0  # 0..1, H-bridge only (PWM)
    motor_active_high: bool = True  # single-pin motor: False for active-low relay boards
    sheet_pin: str = ""  # required for gpio: sheet in the compartment (shared with the gate)
    stack_pin: str = ""  # optional: paper on the input stack (empty vs. jam, idle wait)
    sensor_pull_up: bool = True  # True: sensor pulls the pin LOW when it sees paper
    feed_timeout: float = 5.0  # seconds for a sheet to reach the compartment
    overrun: float = 0.15  # seconds the motor keeps running after the sensor trips

    @classmethod
    def from_env(cls, environ=None) -> "FeederSettings":
        return _from_env(cls, "ADGVS_FEEDER_", environ)


@dataclass(frozen=True)
class GateSettings:
    """Routing gate: a servo flap that sends the sheet to Tray 1 (passed screening) or
    Tray 2 (manual review). ADGVS_GATE_<NAME>.

    route(tray): the servo turns to the tray's angle, an optional eject motor pushes the
    sheet out, and the sheet must leave the compartment (and pass the optional exit sensor)
    within `clear_timeout`, else it is a jam. The servo then returns to `home_angle`, the
    position that keeps the next sheet in the compartment."""

    servo_pin: str = ""  # required for gpio
    tray1_angle: float = -45.0
    tray2_angle: float = 45.0
    home_angle: float = 0.0
    min_angle: float = -90.0  # the servo's travel, mapped to min/max pulse widths
    max_angle: float = 90.0
    min_pulse_ms: float = 1.0  # most hobby servos: 1.0-2.0 ms (some 0.5-2.5)
    max_pulse_ms: float = 2.0
    move_time: float = 0.6  # seconds for the servo to reach an angle
    detach: bool = True  # stop the pulses once in position (no jitter, no hum)
    eject_pin: str = ""  # optional motor/solenoid that pushes the sheet out
    eject_active_high: bool = True
    eject_time: float = 1.5  # longest the eject runs (it stops as soon as the sheet left)
    exit_pin: str = ""  # optional sensor on the way to the trays (sheet passing)
    sensor_pull_up: bool = True
    clear_timeout: float = 5.0  # seconds for the sheet to leave the compartment

    @classmethod
    def from_env(cls, environ=None) -> "GateSettings":
        return _from_env(cls, "ADGVS_GATE_", environ)

    def angle(self, tray: int) -> float:
        return {1: self.tray1_angle, 2: self.tray2_angle}[tray]


@dataclass(frozen=True)
class IntakeSettings:
    """The intake cycle (scan_agent/intake.py). ADGVS_INTAKE_<NAME>."""

    hardware: str = "manual"  # fake | manual (operator, bring-up) | gpio
    # Backend unreachable: the sheet stays in the compartment (never routed without the
    # backend's decision) and the upload is retried every retry_interval; after
    # hold_timeout the station stops and asks the operator to take the sheet out.
    hold_timeout: float = 120.0
    retry_interval: float = 10.0
    http_attempts: int = 4  # tries per request on network errors / 5xx (not on 4xx)
    http_backoff: float = 1.0  # seconds before the 2nd try, doubling (max 8 s)
    clear_timeout: float = 30.0  # after routing: seconds for the compartment to be clear
    resume_after: float = 5.0  # service: compartment clear this long after a stop -> resume
    idle_poll: float = 2.0  # service: seconds between checks for paper while idle
    heartbeat: float = 15.0  # status report to the dashboard at least this often
    jpeg_quality: int = 90  # uploaded page (the backend recompresses it)

    @classmethod
    def from_env(cls, environ=None) -> "IntakeSettings":
        return _from_env(cls, "ADGVS_INTAKE_", environ)


def _positive(problems: list[str], prefix: str, settings, *names: str) -> None:
    for name in names:
        if not getattr(settings, name) > 0:
            problems.append(f"{prefix}{name.upper()} must be > 0")


def validate_intake(intake: IntakeSettings, feeder: FeederSettings | None = None,
                    gate: GateSettings | None = None, *, feeder_kind: str | None = None,
                    gate_kind: str | None = None) -> None:
    """Raise ConfigError listing every problem. Pins are checked for the GPIO parts
    (`feeder_kind` / `gate_kind` default to intake.hardware)."""
    problems: list[str] = []
    feeder_kind, gate_kind = feeder_kind or intake.hardware, gate_kind or intake.hardware
    for name, value in (("ADGVS_INTAKE_HARDWARE", intake.hardware), ("feeder", feeder_kind),
                        ("gate", gate_kind)):
        if value not in HARDWARE_KINDS:
            problems.append(f"{name}={value!r}: one of {', '.join(HARDWARE_KINDS)}")
    if "fake" in (feeder_kind, gate_kind) and feeder_kind != gate_kind:
        problems.append("fake hardware cannot be mixed with manual or gpio")
    _positive(problems, "ADGVS_INTAKE_", intake, "hold_timeout", "retry_interval",
              "http_attempts", "clear_timeout", "resume_after", "idle_poll", "heartbeat")
    if intake.http_backoff < 0:
        problems.append("ADGVS_INTAKE_HTTP_BACKOFF must be >= 0")
    if not 30 <= intake.jpeg_quality <= 100:
        problems.append("ADGVS_INTAKE_JPEG_QUALITY must be 30-100")
    if "gpio" in (feeder_kind, gate_kind):
        problems += _gpio_problems(feeder or FeederSettings(), gate or GateSettings(),
                                   use_feeder=feeder_kind == "gpio", use_gate=gate_kind == "gpio")
    if problems:
        raise ConfigError(problems)


def _gpio_problems(feeder: FeederSettings, gate: GateSettings, *, use_feeder: bool = True,
                   use_gate: bool = True) -> list[str]:
    problems: list[str] = []
    pins: dict[str, str] = {}  # canonical pin -> the setting that uses it
    # The compartment sensor is shared: the GPIO feeder stops on it, the GPIO gate waits for
    # it to clear (and with a manual feeder it confirms the operator's sheet).
    required = {"ADGVS_FEEDER_SHEET_PIN"}
    if use_feeder:
        required.add("ADGVS_FEEDER_MOTOR_PIN")
    if use_gate:
        required.add("ADGVS_GATE_SERVO_PIN")
    groups = [("ADGVS_FEEDER_", feeder, ("motor_pin", "motor_backward_pin", "motor_enable_pin",
                                         "sheet_pin", "stack_pin") if use_feeder
               else ("sheet_pin",))]
    if use_gate:
        groups.append(("ADGVS_GATE_", gate, ("servo_pin", "eject_pin", "exit_pin")))
    for prefix, settings, names in groups:
        for name in names:
            key = prefix + name.upper()
            try:
                pin = parse_pin(getattr(settings, name))
            except ValueError as e:
                problems.append(f"{key}: {e}")
                continue
            if pin is None:
                if key in required:
                    problems.append(f"{key} is required for the GPIO feeder/gate")
            elif pin in pins:
                problems.append(f"{key}: {pin} is already used by {pins[pin]}")
            else:
                pins[pin] = key
    if use_feeder:
        problems += _feeder_problems(feeder)
    if use_gate:
        problems += _gate_problems(gate)
    return problems


def _feeder_problems(feeder: FeederSettings) -> list[str]:
    problems: list[str] = []
    if feeder.motor_enable_pin.strip() and not feeder.motor_backward_pin.strip():
        problems.append("ADGVS_FEEDER_MOTOR_ENABLE_PIN needs ADGVS_FEEDER_MOTOR_BACKWARD_PIN "
                        "(an H-bridge)")
    if not 0 < feeder.motor_speed <= 1:
        problems.append("ADGVS_FEEDER_MOTOR_SPEED must be in (0, 1]")
    _positive(problems, "ADGVS_FEEDER_", feeder, "feed_timeout")
    if feeder.overrun < 0:
        problems.append("ADGVS_FEEDER_OVERRUN must be >= 0")
    return problems


def _gate_problems(gate: GateSettings) -> list[str]:
    problems: list[str] = []
    if not gate.min_angle < gate.max_angle:
        problems.append("ADGVS_GATE_MIN_ANGLE must be below ADGVS_GATE_MAX_ANGLE")
    for name in ("tray1_angle", "tray2_angle", "home_angle"):
        value = getattr(gate, name)
        if not gate.min_angle <= value <= gate.max_angle:
            problems.append(f"ADGVS_GATE_{name.upper()}={value} is outside the servo's travel "
                            f"({gate.min_angle}..{gate.max_angle})")
    if abs(gate.tray1_angle - gate.tray2_angle) < 5:
        problems.append("ADGVS_GATE_TRAY1_ANGLE and ADGVS_GATE_TRAY2_ANGLE must differ by at "
                        "least 5 degrees")
    if not 0.4 <= gate.min_pulse_ms < gate.max_pulse_ms <= 2.6:
        problems.append("servo pulses: need 0.4 <= ADGVS_GATE_MIN_PULSE_MS < "
                        "ADGVS_GATE_MAX_PULSE_MS <= 2.6")
    _positive(problems, "ADGVS_GATE_", gate, "move_time", "eject_time", "clear_timeout")
    return problems
