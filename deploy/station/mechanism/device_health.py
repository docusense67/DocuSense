"""Last observed software checks; ordinary output pins have no movement feedback."""
import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

DEVICES = {
    "gpio": ("GPIO access", [17, 27, 18, 12, 13, 19]),
    "motor": ("Motor driver", [17, 27, 18]),
    "servo1": ("Feeder servo", [12]),
    "servo2": ("Eject servo", [13]),
    "servo3": ("Sorting servo", [19]),
    "camera": ("Raspberry Pi camera", []),
}
PIN_ROLES = {17: 'Motor IN1', 27: 'Motor IN2', 18: 'Motor enable / PWM',
             12: 'Feeder servo', 13: 'Eject servo', 19: 'Sorting servo'}
DEVICES.update({f'gpio_{pin}': (f'GPIO {pin} · {role}', [pin])
                for pin, role in PIN_ROLES.items()})


class HardwareFault(RuntimeError):
    def __init__(self, device):
        self.device = device
        label, pins = DEVICES[device]
        location = "GPIO " + ", ".join(map(str, pins)) if pins else "CSI camera"
        super().__init__(f"{label}: command/access failed ({location}). Check the GPIO driver, pin ownership and permissions."
                         if pins else f"{label}: capture/access failed ({location}). Check camera access and connection.")


class DeviceHealth:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = threading.RLock()
        self.devices = {key: {"id": key, "label": label, "pins": pins,
                             "state": "unchecked", "message": "Not checked yet",
                             "checked_at": None} for key, (label, pins) in DEVICES.items()}
        self.events = []
        if self.path and self.path.is_file():
            try:
                saved = json.loads(self.path.read_text())
                self.events = saved.get("events", [])[-50:]
                for key, state in saved.get("devices", {}).items():
                    if key in self.devices and state.get("state") == "error":
                        self.devices[key].update(state)
            except (ValueError, OSError, TypeError, AttributeError):
                pass

    def _save(self):
        if not self.path:
            return
        try:
            temporary = self.path.with_suffix(".tmp")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                json.dump({"devices": self.devices, "events": self.events}, output)
            temporary.replace(self.path)
        except OSError:
            log.warning("Device health could not be saved; current alerts remain in memory")

    def mark(self, device, state, message):
        with self.lock:
            current = self.devices[device]
            previous = current["state"]
            # A cleanup/stop command cannot clear a fault. Reconnect or a real frame can.
            if previous == "error" and state not in ("error", "configured", "captured"):
                return
            if previous == "error" and device == "camera" and state == "configured":
                return
            now = datetime.now(UTC).isoformat()
            changed = previous != state or current['message'] != message
            current.update(state=state, message=message, checked_at=now)
            kind = "fault" if state == "error" and previous != "error" else (
                "recovered" if previous == "error" and state != "error" else None)
            if kind:
                self.events.append({"id": uuid.uuid4().hex, "device": device, "kind": kind,
                                    "pins": current["pins"], "message": message,
                                    "timestamp": now})
                self.events = self.events[-50:]
            if changed or kind:
                self._save()

    def fail(self, device, message=None):
        self.mark(device, "error", message or str(HardwareFault(device)))

    def outputs_released(self):
        for device in DEVICES:
            if device == 'camera':
                continue
            self.mark(device, "unchecked", "Outputs released; reconnect to check software access")

    def status(self):
        with self.lock:
            return {"devices": [dict(item) for item in self.devices.values()],
                    "events": [dict(item) for item in self.events]}
