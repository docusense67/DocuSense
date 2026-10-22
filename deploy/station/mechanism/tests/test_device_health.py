"""Software fault attribution without asserting physical motor/servo movement."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control import Hardware  # noqa: E402
from device_health import DeviceHealth, HardwareFault  # noqa: E402


class DeviceHealthTests(unittest.TestCase):
    def test_faults_deduplicate_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'health.json'
            health = DeviceHealth(path)
            health.fail('servo1')
            health.fail('servo1')
            self.assertEqual(len(health.events), 1)
            self.assertEqual(health.events[0]['pins'], [12])
            restored = DeviceHealth(path)
            self.assertEqual(restored.devices['servo1']['state'], 'error')
            self.assertEqual(len(restored.events), 1)
            restored.mark('servo1', 'configured', 'GPIO access restored; movement unverified')
            self.assertEqual(restored.events[-1]['kind'], 'recovered')

    def test_cleanup_command_does_not_clear_fault(self):
        health = DeviceHealth()
        health.fail('motor')
        health.mark('motor', 'command_sent', 'Stop requested')
        self.assertEqual(health.devices['motor']['state'], 'error')
        self.assertEqual(len(health.events), 1)

    def test_camera_recovery_requires_a_captured_frame(self):
        health = DeviceHealth()
        health.fail('camera')
        health.mark('camera', 'configured', 'Camera opened')
        self.assertEqual(health.devices['camera']['state'], 'error')
        health.mark('camera', 'captured', 'Frame captured successfully')
        self.assertEqual(health.events[-1]['kind'], 'recovered')

    def test_servo_command_failure_identifies_its_pin(self):
        class BrokenServo:
            @property
            def angle(self):
                return None

            @angle.setter
            def angle(self, value):
                raise OSError('Driver unavailable')

        hardware = Hardware.__new__(Hardware)
        hardware.health = DeviceHealth()
        hardware.enable, hardware.in1, hardware.in2 = Mock(), Mock(), Mock()
        hardware.first = BrokenServo()
        with self.assertRaises(HardwareFault) as caught:
            hardware.apply('servo1', 90)
        self.assertEqual(caught.exception.device, 'servo1')
        self.assertEqual(hardware.health.events[-1]['pins'], [12])

    def test_closed_pin_is_detected_without_actuation(self):
        def output(closed=False):
            return SimpleNamespace(closed=closed, value=0, pin=SimpleNamespace(state=False))
        hardware = Hardware.__new__(Hardware)
        hardware.health, hardware.closed = DeviceHealth(), False
        hardware.enable, hardware.in1, hardware.in2 = output(), output(), output()
        hardware.first, hardware.second, hardware.third = output(), output(True), output()
        with self.assertRaises(HardwareFault) as caught:
            hardware.check_health()
        self.assertEqual(caught.exception.device, 'servo2')
        self.assertEqual(hardware.health.devices['gpio_13']['state'], 'error')
        self.assertTrue(any(event['pins'] == [13] for event in hardware.health.events))

    def test_live_readback_changes_from_idle_to_signal_and_refreshes_timestamp(self):
        hardware = Hardware.__new__(Hardware)
        hardware.health, hardware.closed = DeviceHealth(), False
        def output(value):
            return SimpleNamespace(closed=False, value=value, pin=SimpleNamespace(state=False))
        hardware.enable, hardware.in1, hardware.in2 = output(0), output(0), output(1)
        hardware.first, hardware.second, hardware.third = output(None), output(None), output(None)
        hardware.check_health()
        before = hardware.health.devices['motor']['checked_at']
        self.assertEqual(hardware.health.devices['motor']['state'], 'idle')
        hardware.enable.value = 0.95
        hardware.first.value = 0
        hardware.check_health()
        self.assertEqual(hardware.health.devices['motor']['state'], 'signal_active')
        self.assertEqual(hardware.health.devices['servo1']['state'], 'signal_active')
        self.assertNotEqual(hardware.health.devices['motor']['checked_at'], before)
        self.assertEqual(hardware.health.events, [])

    def test_every_pin_is_checked_even_with_multiple_failures(self):
        def output(value=0, closed=False):
            return SimpleNamespace(closed=closed, value=value, pin=SimpleNamespace(state=False))
        hardware = Hardware.__new__(Hardware)
        hardware.health, hardware.closed = DeviceHealth(), False
        hardware.enable, hardware.in1, hardware.in2 = output(0.95), output(closed=True), output(1)
        hardware.first, hardware.second, hardware.third = output(closed=True), output(None), output(closed=True)
        with self.assertRaises(HardwareFault):
            hardware.check_health()
        for pin in (17, 12, 19):
            self.assertEqual(hardware.health.devices[f'gpio_{pin}']['state'], 'error')
        for pin in (18, 27, 13):
            self.assertIsNotNone(hardware.health.devices[f'gpio_{pin}']['checked_at'])
            self.assertNotEqual(hardware.health.devices[f'gpio_{pin}']['state'], 'error')
        pin_faults = [event for event in hardware.health.events if event['device'].startswith('gpio_')]
        self.assertEqual({event['pins'][0] for event in pin_faults}, {17, 12, 19})
        count = len(hardware.health.events)
        with self.assertRaises(HardwareFault):
            hardware.check_health()
        self.assertEqual(len(hardware.health.events), count)

    def test_all_six_pin_signals_and_release_are_reported(self):
        def output(value):
            return SimpleNamespace(closed=False, value=value, pin=SimpleNamespace(state=False))
        hardware = Hardware.__new__(Hardware)
        hardware.health, hardware.closed = DeviceHealth(), False
        hardware.enable, hardware.in1, hardware.in2 = output(0.95), output(0), output(1)
        hardware.first, hardware.second, hardware.third = output(0), output(None), output(1)
        hardware.check_health()
        expected = {17: 'idle', 27: 'signal_active', 18: 'signal_active',
                    12: 'signal_active', 13: 'idle', 19: 'signal_active'}
        for pin, state in expected.items():
            self.assertEqual(hardware.health.devices[f'gpio_{pin}']['state'], state)
        self.assertIn('95%', hardware.health.devices['gpio_18']['message'])
        hardware.health.outputs_released()
        for pin in expected:
            self.assertEqual(hardware.health.devices[f'gpio_{pin}']['state'], 'unchecked')

    def test_real_gpiozero_composite_servos_are_checked_without_false_pin_error(self):
        # Exercise the installed library with mock pins, not a fake Servo shape.
        from camera_runtime import enable_system_camera_packages
        enable_system_camera_packages()
        import warnings
        from contextlib import ExitStack
        from gpiozero import OutputDevice, PWMOutputDevice, Servo, AngularServo
        from gpiozero.pins.mock import MockFactory, MockPWMPin
        factory = MockFactory(pin_class=MockPWMPin)
        hardware = Hardware.__new__(Hardware)
        hardware.health, hardware.closed = DeviceHealth(), False
        with warnings.catch_warnings(), ExitStack() as stack:
            warnings.simplefilter('ignore')
            hardware.enable = stack.enter_context(PWMOutputDevice(18, initial_value=0, pin_factory=factory))
            hardware.in1 = stack.enter_context(OutputDevice(17, initial_value=False, pin_factory=factory))
            hardware.in2 = stack.enter_context(OutputDevice(27, initial_value=False, pin_factory=factory))
            hardware.first = stack.enter_context(AngularServo(12, initial_angle=None, pin_factory=factory))
            hardware.second = stack.enter_context(Servo(13, initial_value=None, pin_factory=factory))
            hardware.third = stack.enter_context(AngularServo(19, initial_angle=None, pin_factory=factory))
            hardware.check_health()
            for pin in (17, 27, 18, 12, 13, 19):
                self.assertEqual(hardware.health.devices[f'gpio_{pin}']['state'], 'idle')
            hardware.first.angle = 45
            hardware.third.angle = 90
            hardware.check_health()
            self.assertEqual(hardware.health.devices['gpio_12']['state'], 'signal_active')
            self.assertEqual(hardware.health.devices['gpio_19']['state'], 'signal_active')
            self.assertEqual(hardware.health.events, [])
            hardware.second.pwm_device.close()
            with self.assertRaises(HardwareFault):
                hardware.check_health()
            self.assertEqual(hardware.health.devices['gpio_13']['state'], 'error')
            self.assertNotEqual(hardware.health.devices['gpio_19']['state'], 'error')
        factory.close()


if __name__ == '__main__':
    unittest.main()
