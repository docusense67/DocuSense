"""Loopback-only browser controls for the Pi paper-feed mechanism."""

import argparse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import secrets
import signal
import threading
from time import monotonic
from urllib.parse import urlsplit

from control import Controller, CycleSettings, Hardware
from device_health import DeviceHealth, HardwareFault, DEVICES
from camera_focus import CameraFocus

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
LEASE_SECONDS = 4.0
log = logging.getLogger("mechanism")


class SimulatedHardware:
    backend_name = "Simulation — no GPIO"

    def apply(self, action, value=0):
        log.info("SIMULATION %s %s", action, value)

    def stop_all(self):
        log.info("SIMULATION stop all")

    def release_second(self):
        pass

    def close(self):
        self.stop_all()


class Machine:
    def __init__(self, simulate=False, health_file=None):
        self.simulate = simulate
        self.health = DeviceHealth(health_file)
        self.focus = CameraFocus()
        from camera_preview import CameraPreview
        self.preview = CameraPreview(self)
        self.lock = threading.RLock()
        self.hardware = None
        self.controller = None
        self.gpio_lock = None
        self.error = None
        self.last_seen = monotonic()
        self.lease_active = False
        self.settings = CycleSettings()
        self.workflow = None
        if os.environ.get("ADGVS_MECHANISM_KEY"):
            from workflow import Workflow
            self.workflow = Workflow(self)
            self.workflow.restore()

    def connect(self):
        with self.lock:
            if self.hardware is not None:
                return
            try:
                if self.simulate:
                    hardware = SimulatedHardware()
                    for device in DEVICES:
                        if device == 'camera':
                            continue
                        self.health.mark(device, "simulation", "Simulation; no physical GPIO check")
                else:
                    import fcntl

                    self.gpio_lock = (ROOT / ".gpio.lock").open("a")
                    fcntl.flock(self.gpio_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    hardware = Hardware(health=self.health)
                self.hardware = hardware
                self.controller = Controller(hardware)
                self.last_seen = monotonic()
                self.error = None
                log.info("Connected: %s", hardware.backend_name)
            except Exception as error:
                if not isinstance(error, HardwareFault):
                    self.health.fail("gpio", "GPIO access failed. Check permissions and close other GPIO programs.")
                self._unlock()
                self.health.outputs_released()
                raise

    def _unlock(self):
        if self.gpio_lock is not None:
            self.gpio_lock.close()
            self.gpio_lock = None

    def disconnect(self):
        with self.lock:
            if self.workflow:
                self.workflow.stop()
            hardware = self.hardware
            self.hardware = self.controller = None
            self.lease_active = False
            try:
                if hardware is not None:
                    hardware.close()
            finally:
                self._unlock()
                self.health.outputs_released()

    def command(self, name, data=None):
        data = {} if data is None else data
        with self.lock:
            if self.workflow:
                if name in ("stop", "disconnect"):
                    self.workflow.stop()
                elif name != "heartbeat" and (self.workflow.active or self.workflow.held):
                    raise ValueError("Camera cycle active or paper held. Stop and remove paper first.")
            if name == "connect":
                self.connect()
                return
            if name == "disconnect":
                self.disconnect()
                return
            if name == "heartbeat":
                self.last_seen = monotonic()
                return
            if self.controller is None:
                if name == "stop":
                    return
                raise ValueError("Connect the hardware first.")
            now = monotonic()
            if name == "stop":
                self.controller.stop(now)
            elif name == "cycle":
                if self.controller.state != "idle":
                    raise ValueError("Stop the active movement before starting another cycle.")
                settings = CycleSettings(**data)
                settings.validate()
                self.controller.start_cycle(settings, now)
                self.settings = settings
            elif name == "motor":
                if self.controller.state not in ("idle", "continuous"):
                    raise ValueError("A cycle is active. Use Stop All first.")
                self.controller.toggle_motor(now)
            elif name in ("test-motor", "test-servo1", "test-servo2", "test-servo3", "up", "down"):
                if self.controller.state != "idle":
                    raise ValueError("Stop the active movement before testing another device.")
                if name == "test-motor":
                    self.controller.start_motor_test(now)
                elif name == 'test-servo1':
                    self.controller.start_first_servo_test(now)
                elif name == "test-servo2":
                    self.controller.start_second_servo_test(now)
                else:
                    angle = data.get("angle")
                    if isinstance(angle, bool) or not isinstance(angle, (int, float)):
                        raise ValueError("Enter an angle from 0 to 180.")
                    if name == 'test-servo3':
                        self.controller.start_third_servo_test(angle, now)
                    else:
                        self.controller.move_up_down(angle)
            else:
                raise ValueError("Unknown command.")
            self.last_seen = now
            self.lease_active = self.controller.state in ("cycle", "continuous") or name in ("up", "down")
            self.error = None

    def tick(self, now):
        with self.lock:
            try:
                if self.lease_active and now - self.last_seen > LEASE_SECONDS:
                    if self.workflow:
                        self.workflow.stop()
                    if self.controller:
                        self.controller.stop(now)
                    self.lease_active = False
                    self.error = "Browser connection lost. Movement stopped."
                    log.warning(self.error)
                if self.controller:
                    finishing_test = self.controller.state in ('cycle', 'stopping')
                    self.controller.tick(now)
                    if (finishing_test and self.controller.state == 'idle'
                            and not (self.workflow and self.workflow.active)):
                        self.lease_active = False
            except Exception as error:
                log.exception("Movement failed; closing outputs")
                self.error = str(error)
                try:
                    self.disconnect()
                except Exception:
                    log.exception("Additional error while closing outputs")

    def status(self):
        with self.lock:
            c = self.controller
            total = len(c.steps) if c else 0
            busy = c is not None and c.state != "idle"
            return {
                "connected": self.hardware is not None,
                "mode": "simulation" if self.simulate else "hardware",
                "backend": self.hardware.backend_name if self.hardware else None,
                "state": c.state if c else "disconnected",
                "label": c.label if c else "Connect to start",
                "error": self.error,
                "step": min(c.index + 1, total) if c and total else 0,
                "total_steps": total,
                "remaining": max(0, c.deadline - monotonic()) if c and c.state == "cycle" else 0,
                "busy": busy or bool(self.workflow and self.workflow.active),
                "workflow": self.workflow.status() if self.workflow else None,
                "camera_focus": self.focus.status(),
                "settings": asdict(self.settings),
                "pins": {"motor_in1": 17, "motor_in2": 27, "motor_enable": 18,
                         "servo1": 12, "servo2": 13, "up_down": 19},
            }


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, machine):
        self.machine = machine
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.allowed_hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}
        self.allowed_origins = {f"http://{host}" for host in self.allowed_hosts}


class Handler(BaseHTTPRequestHandler):
    server_version = "ADGVSMechanism/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, dict):
            body = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        if self.headers.get("Host", "").lower() not in self.server.allowed_hosts:
            self.reply(403, {"error": "Use the printed localhost address."})
            return False
        return True

    def do_GET(self):
        if not self.valid_host():
            return
        path = urlsplit(self.path).path
        if path == "/integration/status":
            if self.integration_authorized():
                self.reply(200, self.server.machine.status())
        elif path == '/integration/camera/frame':
            if self.integration_authorized():
                frame, timestamp, error = self.server.machine.preview.request_frame()
                if frame is None:
                    self.reply(503, {'error': error})
                else:
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', str(len(frame)))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Frame-Time', timestamp)
                    self.end_headers()
                    self.wfile.write(frame)
        elif path == "/api/status":
            self.reply(200, {**self.server.machine.status(), "csrf_token": self.server.csrf_token})
        elif path == "/api/health":
            self.reply(200, {"status": "ok", "mode": "simulation" if self.server.machine.simulate else "hardware"})
        else:
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/app.css": ("app.css", "text/css; charset=utf-8")}
            asset = assets.get(path)
            if asset is None:
                self.reply(404, {"error": "Not found"})
            else:
                self.reply(200, (WEB / asset[0]).read_bytes(), asset[1])

    def integration_authorized(self):
        expected = os.environ.get("ADGVS_MECHANISM_KEY", "")
        supplied = self.headers.get("X-ADGVS-Bridge-Key", "")
        if not expected or not secrets.compare_digest(expected, supplied):
            self.reply(403, {"error": "Integration key required."})
            return False
        return True

    def do_POST(self):
        if not self.valid_host():
            return
        origin = self.headers.get("Origin")
        if (origin is not None and origin not in self.server.allowed_origins
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self.reply(403, {"error": "Cross-site control requests are refused."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                raise ValueError("Invalid request size.")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("JSON is required.")
            body = json.loads(self.rfile.read(size), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite number")))
            if not isinstance(body, dict):
                raise ValueError("A JSON object is required.")
            if urlsplit(self.path).path == "/integration/action":
                if not self.integration_authorized():
                    return
                machine = self.server.machine
                with machine.lock:
                    workflow = machine.workflow
                    if workflow is None:
                        raise ValueError("Camera integration is disabled.")
                    action = body.pop("action", "")
                    if action == "scan":
                        workflow.start(body)
                    elif action == "acknowledge":
                        workflow.acknowledge()
                    elif action in ("stop", "heartbeat", "connect", "disconnect", "up",
                                    "test-motor", "test-servo1", "test-servo2", "test-servo3"):
                        machine.command(action, body)
                    else:
                        raise ValueError("Unknown integration action.")
                self.reply(200, machine.status())
                return
            token = self.headers.get("X-Mechanism-Token") or body.pop("csrf_token", "")
            if not isinstance(token, str) or not secrets.compare_digest(token, self.server.csrf_token):
                self.reply(403, {"error": "Refresh the page before sending controls."})
                return
            path = urlsplit(self.path).path
            commands = {f"/api/{name}": name for name in (
                "connect", "disconnect", "cycle", "motor", "stop", "test-motor",
                "test-servo2", "up", "down", "heartbeat",
            )}
            command = commands.get(path)
            if command is None:
                self.reply(404, {"error": "Unknown command."})
                return
            self.server.machine.command(command, body)
            self.reply(200, self.server.machine.status())
        except (ValueError, TypeError) as error:
            self.reply(400, {"error": str(error)})
        except Exception as error:
            log.exception("Hardware command failed")
            self.server.machine.error = str(error)
            try:
                self.server.machine.disconnect()
            except Exception:
                log.exception("Additional shutdown error")
            self.reply(503, {"error": f"Hardware unavailable: {error}. Close other motor/servo programs and check GPIO access."})

    def log_message(self, fmt, *args):
        if "/api/status" not in self.requestline and "/api/heartbeat" not in self.requestline:
            log.info(fmt, *args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--simulate", action="store_true", help="Explicit simulation; does not claim GPIO.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be from 1 to 65535.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), RotatingFileHandler(ROOT / "server.log", maxBytes=500_000, backupCount=1)])
    machine = Machine(simulate=args.simulate)
    server = Server(args.port, machine)
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())

    def update():
        while not stopped.wait(0.01):
            machine.tick(monotonic())

    worker = threading.Thread(target=update, daemon=True)
    def report_presence():
        import urllib.request
        key = os.environ.get("ADGVS_CAMERA_STATION_KEY")
        base = os.environ.get("ADGVS_CAMERA_API_URL", "http://127.0.0.1:8000")
        if not key or machine.simulate:
            return
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while not stopped.is_set():
            try:
                with machine.lock:
                    active = bool(machine.workflow and machine.workflow.active)
                    detail = "Camera mechanism service online; hardware checked when a cycle starts"
                request = urllib.request.Request(base.rstrip("/") + "/api/station/status",
                    data=json.dumps({"state": "scanning" if active else "ready", "detail": detail}).encode(),
                    headers={"Content-Type": "application/json", "X-Station-Key": key}, method="POST")
                with opener.open(request, timeout=5):
                    pass
            except Exception:
                log.warning("Station presence unavailable; check local backend and station key")
            stopped.wait(10)

    presence = threading.Thread(target=report_presence, daemon=True)
    presence.start()
    http = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    http.start()
    print(f"Mechanism controls: http://localhost:{server.server_port}", flush=True)
    print("SIMULATION — no hardware" if args.simulate else "Hardware mode — click Connect Hardware in the page", flush=True)
    try:
        stopped.wait()
    finally:
        stopped.set()
        machine.preview.close()
        server.shutdown()
        worker.join(timeout=2)
        machine.disconnect()
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except OSError as error:
        raise SystemExit(f"Cannot start localhost server: {error}. Run start.sh in the Pi Terminal.")
