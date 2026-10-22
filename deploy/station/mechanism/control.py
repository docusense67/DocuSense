"""Self-contained Pi mechanism controller. GPIO is opened only on connection."""

from contextlib import ExitStack
from dataclasses import dataclass
import logging
from math import isfinite
from time import sleep

from device_health import HardwareFault, PIN_ROLES

SPEED = 1.0  # L298N ENA: 100% duty while the motor runs.
REVERSE_SECONDS = 5.0
SECOND_SERVO_NEUTRAL = 0.0
SECOND_SERVO_NEUTRAL_SECONDS = 0.3
SECOND_SERVO_SECONDS = 8.0
SECOND_SERVO_SPEED = 1.0  # 2 ms / 50 Hz / 10% duty; maximum servo command.
SERVO_ANGLES = (90, 180, 90)
SERVO_STEP_SECONDS = 0.5
STOP_SECONDS = 2.0

UP_DOWN_PIN = 19  # Physical pin 35.
TRAY_UP_ANGLE = 180
TRAY_DOWN_ANGLE = 90
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CycleSettings:
    up_angle: float = TRAY_UP_ANGLE
    down_angle: float = TRAY_DOWN_ANGLE
    down_seconds: float = 1.0
    after_feed: str = "servo2"

    def validate(self):
        for angle in (self.up_angle, self.down_angle):
            if not isfinite(angle) or not 0 <= angle <= 180:
                raise ValueError("UP and DOWN angles must be between 0 and 180.")
        if not isfinite(self.down_seconds) or not 0.1 <= self.down_seconds <= 10:
            raise ValueError("DOWN hold time must be between 0.1 and 10 seconds.")
        if self.after_feed not in ("motor", "servo2"):
            raise ValueError("Select the DC motor or second servo as the paper feed.")


@dataclass(frozen=True)
class Step:
    label: str
    seconds: float
    action: str
    value: float = 0


def cycle_steps(settings):
    settings.validate()
    up_down = [
        Step("Up/down: UP", 0.5, "servo3", settings.up_angle),
        Step("Up/down: DOWN", settings.down_seconds, "servo3", settings.down_angle),
        Step("Up/down: return UP", 0.5, "servo3", settings.up_angle),
    ]
    steps = [
        Step(f"Servo 1: {angle} degrees", SERVO_STEP_SECONDS, "servo1", angle)
        for angle in SERVO_ANGLES
    ]
    steps.append(Step("Motor: feeding backward", REVERSE_SECONDS, "motor", SPEED))
    if settings.after_feed == "motor":
        steps.extend(up_down)
    steps.extend([
        Step("Servo 2: preparing", SECOND_SERVO_NEUTRAL_SECONDS, "servo2", SECOND_SERVO_NEUTRAL),
        Step("Servo 2: rotating", SECOND_SERVO_SECONDS, "servo2", SECOND_SERVO_SPEED),
        Step("Servo 2: stopping", SECOND_SERVO_NEUTRAL_SECONDS, "servo2", SECOND_SERVO_NEUTRAL),
        Step("Servo 2: stopped", 0, "release_servo2"),
    ])
    if settings.after_feed == "servo2":
        steps.extend(up_down)
    steps.append(Step("Cycle complete: pause", STOP_SECONDS, "stop"))
    return steps


class Hardware:
    """Claims output pins only: no physical button inputs are opened."""

    def __init__(self, health=None):
        from camera_runtime import enable_system_camera_packages

        enable_system_camera_packages()
        from gpiozero import OutputDevice, PWMOutputDevice, Servo
        from gpiozero.pins.mock import MockFactory

        self.stack = ExitStack()
        self.health = health
        self.closed = False
        try:
            self.enable = self.open_output("motor", lambda: PWMOutputDevice(
                18, frequency=1000, initial_value=0), pin=18)
            if isinstance(self.enable.pin.factory, MockFactory):
                raise RuntimeError("Mock GPIO cannot drive hardware. Use --simulate explicitly for previews.")
            self.in1 = self.open_output("motor", lambda: OutputDevice(17, initial_value=False), pin=17)
            self.in2 = self.open_output("motor", lambda: OutputDevice(27, initial_value=False), pin=27)
            self.first = self.open_output("servo1", lambda: self.position_servo(12), pin=12)
            self.second = self.open_output("servo2", lambda: Servo(
                13, initial_value=None, min_pulse_width=0.001,
                max_pulse_width=0.002, frame_width=0.020,
            ), pin=13)
            self.third = self.open_output("servo3", lambda: self.position_servo(UP_DOWN_PIN), pin=UP_DOWN_PIN)
            if self.health:
                for device in ("gpio", "motor", "servo1", "servo2", "servo3"):
                    self.health.mark(device, "configured", "GPIO configured; physical movement not verified")
            log.info("GPIO outputs opened using %s: motor IN1=17 IN2=27 ENA=18; servos=12,13,19", self.backend_name)
        except Exception:
            self.stack.close()
            raise

    def open_output(self, device, create, pin=None):
        try:
            output = self.stack.enter_context(create())
            if self.health and pin is not None:
                self.health.mark(f'gpio_{pin}', 'configured', f'GPIO {pin} configured; connection unverified')
            return output
        except Exception as error:
            if self.health:
                if pin is not None:
                    self.health.fail(f'gpio_{pin}')
                self.health.fail(device)
            raise HardwareFault(device) from error

    def output_command(self, device, command):
        try:
            command()
            if self.health:
                self.health.mark(device, "command_sent", "Command sent; physical movement not verified")
        except Exception as error:
            if self.health:
                self.health.fail(device)
            raise HardwareFault(device) from error

    def check_health(self):
        groups = {'motor': [(18, self.enable), (17, self.in1), (27, self.in2)],
                  'servo1': [(12, self.first)], 'servo2': [(13, self.second)],
                  'servo3': [(19, self.third)]}
        faults = []
        for device, outputs in groups.items():
            values, failed = [], []
            for pin, output in outputs:
                try:
                    if self.closed or output.closed:
                        raise RuntimeError('GPIO output is closed')
                    # Servo/AngularServo are CompositeDevices; their GPIO belongs
                    # to pwm_device. Motor outputs expose pin directly.
                    gpio_output = getattr(output, 'pwm_device', output)
                    if gpio_output.closed:
                        raise RuntimeError('GPIO PWM output is closed')
                    _ = gpio_output.pin.state  # Software readback, not external wiring feedback.
                    value = output.value
                    values.append(value)
                    if self.health:
                        active = value is not None if pin in (12, 13, 19) else bool(value)
                        signal = ('PWM signal enabled' if active else 'PWM detached') if pin in (12, 13, 19) else (
                            f'PWM duty {float(value or 0):.0%}' if pin == 18 else 'HIGH' if value else 'LOW')
                        self.health.mark(f'gpio_{pin}', 'signal_active' if active else 'idle',
                                         f'{PIN_ROLES[pin]}: {signal}; physical connection unverified')
                except Exception:
                    failed.append(pin)
                    if self.health:
                        self.health.fail(f'gpio_{pin}')
            if failed:
                faults.extend((device, pin) for pin in failed)
                if self.health:
                    self.health.fail(device, f'GPIO access failed on {", ".join(map(str, failed))}. Check device access and wiring.')
                continue
            try:
                if self.health:
                    if device == 'motor':
                        active = bool(values[0] and (values[1] or values[2]))
                    else:
                        active = values[0] is not None
                    self.health.mark(device, 'signal_active' if active else 'idle',
                                     'Output signal enabled; physical movement unverified' if active
                                     else 'Output idle; physical connection unverified')
            except Exception as error:
                if self.health:
                    self.health.fail(device)
                raise HardwareFault(device) from error
        if faults:
            if self.health:
                self.health.fail('gpio', 'GPIO access failed on pins: ' + ', '.join(str(pin) for _, pin in faults))
            raise HardwareFault(faults[0][0])
        if self.health:
            self.health.mark('gpio', 'configured', 'GPIO readback checked successfully')

    @staticmethod
    def position_servo(pin):
        from gpiozero import AngularServo

        return AngularServo(
            pin, initial_angle=None, min_angle=0, max_angle=180,
            min_pulse_width=0.001, max_pulse_width=0.002, frame_width=0.020,
        )

    @property
    def backend_name(self):
        return type(self.enable.pin.factory).__name__

    def stop_motor(self):
        def stop():
            self.enable.off()
            self.in1.off()
            self.in2.off()
        self.output_command("motor", stop)

    def apply(self, action, value=0):
        if action != "motor":
            self.stop_motor()
        if action == "motor":
            def feed():
                self.in1.off()
                self.in2.on()
                self.enable.value = value
            self.output_command("motor", feed)
        elif action == "servo1":
            self.output_command("servo1", lambda: setattr(self.first, "angle", value))
        elif action == "servo2":
            self.output_command("servo2", lambda: setattr(self.second, "value", value))
        elif action == "release_servo2":
            self.output_command("servo2", self.second.detach)
        elif action == "release_servo1":
            self.output_command("servo1", self.first.detach)
        elif action == "release_servo3":
            self.output_command("servo3", self.third.detach)
        elif action == "servo3":
            self.output_command("servo3", lambda: setattr(self.third, "angle", value))
        elif action == "stop":
            return
        else:
            raise ValueError(f"Unknown movement: {action}")
        log.info("Output command: %s value=%s", action, value)

    def stop_all(self):
        log.info("Stop all requested")
        try:
            self.stop_motor()
        finally:
            try:
                self.output_command("servo2", lambda: setattr(self.second, "value", SECOND_SERVO_NEUTRAL))
            finally:
                try:
                    self.output_command("servo1", self.first.detach)
                finally:
                    self.output_command("servo3", self.third.detach)

    def release_second(self):
        self.output_command("servo2", self.second.detach)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.stop_all()
            sleep(SECOND_SERVO_NEUTRAL_SECONDS)
        finally:
            self.stack.close()


class Controller:
    def __init__(self, hardware):
        self.hardware = hardware
        self.state = "idle"
        self.label = "Ready"
        self.steps = []
        self.index = 0
        self.deadline = 0.0

    def start_cycle(self, settings, now):
        if self.state != "idle":
            return False
        return self._start_steps(cycle_steps(settings), now)

    def start_motor_test(self, now):
        if self.state != "idle":
            return False
        return self._start_steps([
            Step("Testing motor backward", REVERSE_SECONDS, "motor", SPEED),
            Step("Motor test: stopped", 0, "stop"),
        ], now)

    def start_second_servo_test(self, now):
        if self.state != "idle":
            return False
        return self._start_steps([
            Step("Servo 2 test: preparing", SECOND_SERVO_NEUTRAL_SECONDS, "servo2", SECOND_SERVO_NEUTRAL),
            Step("Servo 2 test: rotating", SECOND_SERVO_SECONDS, "servo2", SECOND_SERVO_SPEED),
            Step("Servo 2 test: stopping", SECOND_SERVO_NEUTRAL_SECONDS, "servo2", SECOND_SERVO_NEUTRAL),
            Step("Servo 2 test: signal released", 0, "release_servo2"),
        ], now)

    def start_first_servo_test(self, now):
        if self.state != 'idle':
            return False
        return self._start_steps([
            *[Step(f'Feeder servo: {angle} degrees', SERVO_STEP_SECONDS, 'servo1', angle)
              for angle in SERVO_ANGLES],
            Step('Feeder servo: signal released', 0, 'release_servo1'),
        ], now)

    def start_third_servo_test(self, angle, now):
        if self.state != 'idle':
            return False
        if not isfinite(angle) or not 0 <= angle <= 180:
            raise ValueError('Servo angle must be between 0 and 180.')
        return self._start_steps([
            Step(f'Sorting servo: {angle:g} degrees', 0.7, 'servo3', angle),
            Step('Sorting servo: signal released', 0, 'release_servo3'),
        ], now)

    def _start_steps(self, steps, now):
        self.steps = steps
        self.index = 0
        self.state = "cycle"
        self._enter_step(now)
        return True

    def _enter_step(self, now):
        step = self.steps[self.index]
        log.info("Cycle step %d/%d: %s (%.2fs)", self.index + 1, len(self.steps), step.label, step.seconds)
        self.label = step.label
        self.hardware.apply(step.action, step.value)
        self.deadline = now + step.seconds

    def tick(self, now):
        if self.state == "stopping" and now >= self.deadline:
            self.hardware.release_second()
            self.state, self.label = "idle", "Stopped — ready"
        elif self.state == "cycle" and now >= self.deadline:
            self.index += 1
            if self.index == len(self.steps):
                self.hardware.apply("stop")
                self.state, self.label = "idle", "Cycle complete — ready"
            else:
                self._enter_step(now)

    def toggle_motor(self, now):
        if self.state == "continuous":
            self.stop(now)
        elif self.state == "idle":
            self.hardware.apply("motor", SPEED)
            self.state, self.label = "continuous", "Motor running backward continuously"

    def stop(self, now):
        self.hardware.stop_all()
        self.steps = []
        self.state, self.label = "stopping", "Stopping all movement"
        self.deadline = now + SECOND_SERVO_NEUTRAL_SECONDS

    def move_up_down(self, angle):
        if self.state != "idle":
            return False
        if not isfinite(angle) or not 0 <= angle <= 180:
            raise ValueError("Servo angle must be between 0 and 180.")
        self.hardware.apply("servo3", angle)
        self.label = f"Up/down servo: {angle:g} degrees"
        return True
