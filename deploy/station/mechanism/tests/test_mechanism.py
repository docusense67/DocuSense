"""Hardware-free controller and HTTP checks; no sockets are opened."""

from email.message import Message
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import control
import server


class RecordingHardware(server.SimulatedHardware):
    def __init__(self):
        self.events = []
        self.motor = 0
        self.rotation = 0
        self.closed = False

    def apply(self, action, value=0):
        if action != "motor":
            self.motor = 0
        if action in ("servo1", "servo3"):
            assert not self.motor and not self.rotation
        if action == "motor":
            assert not self.rotation
            self.motor = value
        if action == "servo2":
            self.rotation = value
        if action == "release_servo2":
            self.rotation = 0
        self.events.append((action, value))

    def stop_all(self):
        self.motor = self.rotation = 0
        self.events.append(("stop_all", 0))

    def release_second(self):
        self.rotation = 0

    def close(self):
        self.stop_all()
        self.closed = True


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.hardware = RecordingHardware()
        self.controller = control.Controller(self.hardware)

    def test_full_cycle_order_and_timing(self):
        self.controller.start_cycle(control.CycleSettings(), 0)
        durations = []
        while self.controller.state == "cycle":
            step = self.controller.steps[self.controller.index]
            durations.append((step.action, step.value, step.seconds))
            self.controller.tick(self.controller.deadline)
        events = self.hardware.events
        self.assertEqual([value for action, value in events if action == "servo1"], [90, 180, 90])
        self.assertEqual([value for action, value in events if action == "servo3"], [180, 90, 180])
        self.assertIn(("motor", 1.0, 5), durations)
        self.assertIn(("servo2", 1.0, 8), durations)
        self.assertIn(("servo2", 0, .3), durations)
        actions = [action for action, _ in events]
        self.assertLess(actions.index("motor"), actions.index("servo2"))
        self.assertLess(actions.index("release_servo2"), actions.index("servo3"))
        self.assertEqual((self.hardware.motor, self.hardware.rotation), (0, 0))

    def test_up_down_can_follow_dc_feed(self):
        steps = control.cycle_steps(control.CycleSettings(after_feed="motor"))
        actions = [step.action for step in steps]
        self.assertEqual(actions[actions.index("motor") + 1], "servo3")

    def test_stop_at_every_stage(self):
        for index in range(len(control.cycle_steps(control.CycleSettings()))):
            self.setUp()
            self.controller.start_cycle(control.CycleSettings(), 0)
            for _ in range(index):
                self.controller.tick(self.controller.deadline)
            self.controller.stop(self.controller.deadline)
            self.assertEqual((self.hardware.motor, self.hardware.rotation), (0, 0))
            self.controller.tick(self.controller.deadline)
            self.assertEqual(self.controller.state, "idle")
            self.assertEqual(self.controller.steps, [])

    def test_individual_tests_and_busy_rejection(self):
        self.assertTrue(self.controller.start_motor_test(0))
        self.assertFalse(self.controller.start_second_servo_test(1))
        self.assertFalse(self.controller.move_up_down(90))
        self.controller.tick(5)
        self.controller.tick(5)
        self.assertEqual(self.hardware.motor, 0)
        self.assertTrue(self.controller.start_second_servo_test(6))
        while self.controller.state == "cycle":
            self.controller.tick(self.controller.deadline)
        self.assertEqual(self.hardware.rotation, 0)

    def test_feeder_and_sorting_tests_release_signals_and_reject_busy_commands(self):
        self.assertTrue(self.controller.start_first_servo_test(0))
        self.assertFalse(self.controller.start_third_servo_test(90, 0))
        while self.controller.state == 'cycle':
            self.controller.tick(self.controller.deadline)
        self.assertEqual([v for a, v in self.hardware.events if a == 'servo1'], [90, 180, 90])
        self.assertIn(('release_servo1', 0), self.hardware.events)
        self.assertTrue(self.controller.start_third_servo_test(75, 10))
        while self.controller.state == 'cycle':
            self.controller.tick(self.controller.deadline)
        self.assertIn(('servo3', 75), self.hardware.events)
        self.assertIn(('release_servo3', 0), self.hardware.events)
        for angle in (-1, 181, float('nan')):
            with self.assertRaises(ValueError):
                self.controller.start_third_servo_test(angle, 11)


class MachineTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        clock = patch.object(server, "monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.hardware = RecordingHardware()
        factory = patch.object(server, "SimulatedHardware", return_value=self.hardware)
        factory.start()
        self.addCleanup(factory.stop)
        self.machine = server.Machine(simulate=True)
        self.addCleanup(self.machine.disconnect)

    def test_disconnected_startup_does_not_open_gpio(self):
        self.assertFalse(self.machine.status()["connected"])
        self.assertEqual(self.hardware.events, [])
        with self.assertRaises(ValueError):
            self.machine.command("cycle")

    def test_cycle_with_heartbeat_completes(self):
        self.machine.command("connect")
        self.machine.command("cycle")
        while self.machine.controller.state == "cycle":
            self.now = self.machine.controller.deadline
            self.machine.command("heartbeat")
            self.machine.tick(self.now)
        self.assertEqual(self.machine.status()["state"], "idle")

    def test_browser_timeout_stops_motor_and_manual_servo(self):
        self.machine.command("connect")
        for command, data in (("motor", {}), ("up", {"angle": 90})):
            self.machine.command(command, data)
            self.now += server.LEASE_SECONDS + .1
            self.machine.tick(self.now)
            self.assertEqual(self.hardware.motor, 0)
            self.assertEqual(self.machine.status()["state"], "stopping")
            self.now += 1
            self.machine.tick(self.now)
            self.assertEqual(self.machine.status()["state"], "idle")

    def test_invalid_settings_do_not_actuate(self):
        self.machine.command("connect")
        for settings in ({"up_angle": -1}, {"down_angle": 181}, {"after_feed": "invalid"}, {"down_seconds": float("nan")}):
            with self.assertRaises(ValueError):
                self.machine.command("cycle", settings)
        self.assertEqual(self.hardware.events, [])

    def test_gpio_connection_failure_releases_lock(self):
        machine = server.Machine()
        with tempfile.TemporaryDirectory() as temp, patch.object(server, "ROOT", Path(temp)), patch.object(server, "Hardware", side_effect=RuntimeError("GPIO busy")):
            with self.assertRaisesRegex(RuntimeError, "GPIO busy"):
                machine.connect()
            self.assertIsNone(machine.gpio_lock)
            self.assertFalse(machine.status()["connected"])

    def test_disconnect_stops_hardware(self):
        self.machine.command("connect")
        self.machine.command("motor")
        self.machine.command("disconnect")
        self.assertTrue(self.hardware.closed)
        self.assertFalse(self.machine.status()["connected"])

    def test_manual_tests_finish_without_false_browser_disconnect(self):
        self.machine.command('connect')
        for action, data in [('test-motor', {}), ('test-servo1', {}),
                             ('test-servo2', {}), ('test-servo3', {'angle': 60})]:
            self.machine.command(action, data)
            while self.machine.controller.state == 'cycle':
                self.now += 0.1
                self.machine.command('heartbeat')
                self.machine.tick(self.now)
            self.assertFalse(self.machine.lease_active)
            self.now += server.LEASE_SECONDS + 1
            self.machine.tick(self.now)
            self.assertIsNone(self.machine.error)
            self.assertEqual(self.machine.controller.state, 'idle')

    def test_manual_servo_test_stops_on_browser_timeout(self):
        self.machine.command('connect')
        self.machine.command('test-servo2')
        self.now += server.LEASE_SECONDS + 1
        self.machine.tick(self.now)
        self.assertEqual(self.hardware.rotation, 0)
        self.assertIn('Browser connection lost', self.machine.error)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.machine = server.Machine(simulate=True)
        self.addCleanup(self.machine.disconnect)
        self.server = SimpleNamespace(
            machine=self.machine, csrf_token="test-token",
            allowed_hosts={"localhost:8765", "127.0.0.1:8765"},
            allowed_origins={"http://localhost:8765", "http://127.0.0.1:8765"},
        )

    def request(self, method, path, data=None, host="localhost:8765", origin="http://localhost:8765", token="test-token", raw=None):
        handler = server.Handler.__new__(server.Handler)
        handler.server = self.server
        handler.path, handler.command = path, method
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.headers = Message()
        handler.headers["Host"] = host
        if origin is not None:
            handler.headers["Origin"] = origin
        if token is not None:
            handler.headers["X-Mechanism-Token"] = token
        payload = raw if raw is not None else json.dumps(data or {}).encode()
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Content-Length"] = str(len(payload))
        handler.rfile, handler.wfile = BytesIO(payload), BytesIO()
        getattr(handler, f"do_{method}")()
        head, body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        return int(head.split(b" ", 2)[1]), head, body

    def test_page_assets_and_health(self):
        for path in ("/", "/app.css", "/app.js", "/api/health"):
            status, head, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertIn(b"X-Content-Type-Options: nosniff", head)
        _, _, body = self.request("GET", "/api/health")
        self.assertEqual(json.loads(body)["mode"], "simulation")

    def test_bad_host_origin_and_token_refused(self):
        self.assertEqual(self.request("GET", "/api/status", host="attacker.example:8765")[0], 403)
        self.assertEqual(self.request("POST", "/api/connect", origin="https://attacker.example")[0], 403)
        self.assertEqual(self.request("POST", "/api/connect", token="bad")[0], 403)
        self.assertFalse(self.machine.status()["connected"])

    def test_connect_and_control_through_http(self):
        self.assertEqual(self.request("POST", "/api/connect")[0], 200)
        self.assertEqual(self.request("POST", "/api/test-servo2")[0], 200)
        self.assertEqual(self.machine.controller.state, "cycle")
        self.assertEqual(self.request("POST", "/api/stop")[0], 200)
        self.assertEqual(self.machine.controller.state, "stopping")

    def test_page_close_beacon_can_stop(self):
        self.machine.command("connect")
        self.machine.command("motor")
        status, _, _ = self.request("POST", "/api/stop", {"csrf_token": "test-token"}, token=None)
        self.assertEqual(status, 200)
        self.assertEqual(self.machine.controller.state, "stopping")

    def test_non_json_numbers_and_unknown_routes_rejected(self):
        self.assertEqual(self.request("POST", "/api/cycle", raw=b'{"up_angle":NaN}')[0], 400)
        self.assertEqual(self.request("GET", "/../control.py")[0], 404)
        self.assertEqual(self.request("POST", "/api/nonexistent")[0], 404)

    def test_hardware_failure_is_visible_and_not_simulated(self):
        with patch.object(self.machine, "connect", side_effect=RuntimeError("GPIO unavailable")):
            status, _, body = self.request("POST", "/api/connect")
        self.assertEqual(status, 503)
        self.assertIn("GPIO unavailable", json.loads(body)["error"])
        self.assertFalse(self.machine.status()["connected"])

    def test_server_binds_only_loopback(self):
        captured = []
        def init(instance, address, handler):
            instance.server_port = address[1]
            captured.append(address)
        with patch.object(server.ThreadingHTTPServer, "__init__", new=init):
            instance = server.Server(8765, self.machine)
        self.assertEqual(captured, [("127.0.0.1", 8765)])
        self.assertIn("localhost:8765", instance.allowed_hosts)


if __name__ == "__main__":
    unittest.main()
