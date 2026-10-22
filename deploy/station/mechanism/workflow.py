"""One-sheet camera workflow; validation decisions come only from the ADGVS API."""
import json
import logging
import os
from pathlib import Path
import threading
from time import monotonic
import uuid
from contextlib import nullcontext

from control import (SERVO_ANGLES, SERVO_STEP_SECONDS, REVERSE_SECONDS, SPEED,
                     SECOND_SERVO_SECONDS, SECOND_SERVO_SPEED, SECOND_SERVO_NEUTRAL,
                     TRAY_UP_ANGLE, TRAY_DOWN_ANGLE)
from camera_runtime import enable_system_camera_packages
from camera_focus import CameraFocus, FocusCancelled, FocusError
from device_health import HardwareFault

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent


class Cancelled(Exception):
    pass


class Workflow:
    @property
    def stage(self):
        return self._stage

    @stage.setter
    def stage(self, value):
        milestones = {'idle': 0, 'preflight': 5, 'feeding': 20, 'capturing': 40,
                      'ocr': 60, 'validating': 75, 'sorting': 90, 'complete': 100}
        self._stage = value
        if value in milestones:
            self.progress = milestones[value]

    def __init__(self, machine):
        self.machine = machine
        self.cancelled = threading.Event()
        self.thread = None
        self.stage = 'idle'
        self.message = 'Ready for one sheet'
        self.result = None
        self.error = None
        self.session_id = None
        self.folder = Path(os.environ.get('ADGVS_CAMERA_FOLDER', str(ROOT / '.camera_pending')))
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.folder.chmod(0o700)
        self.journal = self.folder / 'paper-state.json'
        # An interrupted process must never automatically feed another sheet.
        self.held = self.journal.exists()
        if self.held:
            self.message = 'Previous cycle interrupted. Remove any paper, then acknowledge.'

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def status(self):
        return dict(active=self.active, stage=self.stage, message=self.message,
                    held=self.held, result=self.result, session_id=self.session_id,
                    progress=self.progress, error=self.error)

    def mark_paper(self, held):
        self.held = held
        # Retain a journal rather than deleting scanner files outside the watcher.
        with self.journal.open('w', encoding='utf-8') as file:
            os.chmod(self.journal, 0o600)
            json.dump({'held': held}, file)
            file.flush()
            os.fsync(file.fileno())

    def restore(self):
        if self.journal.exists():
            try:
                self.held = bool(json.loads(self.journal.read_text())['held'])
            except (ValueError, KeyError):
                self.held = True

    def start(self, data):
        if self.machine.simulate:
            raise ValueError('Official camera scans require real hardware, not simulation.')
        if self.active or self.held:
            raise ValueError('Stop the cycle and remove held paper before feeding another sheet.')
        if self.machine.controller and self.machine.controller.state != 'idle':
            raise ValueError('Stop manual movement first.')
        session_id = data.get('session_id')
        if isinstance(session_id, bool) or not isinstance(session_id, int) or session_id < 1:
            raise ValueError('An open single-document session is required.')
        self.cancelled.clear()
        self.result = None
        self.error = None
        self.session_id = session_id
        self.stage = 'preflight'
        self.message = 'Checking camera, OCR and scan session'
        self.machine.error = None
        self.machine.last_seen = monotonic()
        self.machine.lease_active = True
        self.thread = threading.Thread(target=self.run, args=(session_id,), daemon=True)
        self.thread.start()

    def stop(self):
        self.cancelled.set()
        if self.active:
            self.stage = 'stopping'
            self.message = 'Stopped. Remove paper before starting again.'

    def acknowledge(self):
        if self.active:
            raise ValueError('Wait for the stopped capture/OCR operation to finish first.')
        self.mark_paper(False)
        self.cancelled.clear()
        self.machine.error = None
        self.result = None
        self.error = None
        self.stage, self.message = 'idle', 'Paper removed. Ready for one sheet.'

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled()

    def step(self, label, action, value=0, seconds=0, stage='feeding'):
        with self.machine.lock:
            self.check()
            self.stage, self.message = stage, label
            self.machine.hardware.apply(action, value)
        if self.cancelled.wait(seconds):
            raise Cancelled()

    def run(self, session_id):
        camera = client = None
        health = getattr(self.machine, 'health', None)
        device = None
        preview = getattr(self.machine, 'preview', None)
        focus = getattr(self.machine, 'focus', None) or CameraFocus()
        camera_lock = preview.io if preview else nullcontext()
        try:
            # All imports and dependency checks happen before moving paper.
            enable_system_camera_packages()
            import cv2
            import httpx
            import pytesseract
            from picamera2 import Picamera2
            from scan_agent.capture.base import CapturedPage
            from scan_agent.client import BackendClient
            from scan_agent.pipeline import process_stack

            pytesseract.get_tesseract_version()
            base = os.environ.get('ADGVS_CAMERA_API_URL', 'http://127.0.0.1:8000')
            key = os.environ.get('ADGVS_CAMERA_STATION_KEY', '')
            if not key:
                raise ValueError('ADGVS_CAMERA_STATION_KEY is not configured.')
            client = BackendClient(station_key=key, http=httpx.Client(
                base_url=base, timeout=30, trust_env=False, follow_redirects=False))
            session = client.session()
            if session.get('session_id') != session_id or session.get('mode') != 'single':
                raise ValueError('The selected session is not this camera station’s open single session.')
            self.check()
            device = 'camera'
            if preview:
                preview.begin_scan()
            camera = Picamera2()
            camera.configure(camera.create_still_configuration(main={'size': (3280, 2464), 'format': 'RGB888'}))
            focus.configure(camera)
            camera.start()
            if preview:
                preview.attach(camera)
            if health:
                health.mark('camera', 'configured', 'Camera opened; capture not yet verified')
            if self.cancelled.wait(1):
                raise Cancelled()
            with self.machine.lock:
                self.check()
                device = 'gpio'
                self.machine.connect()
                self.mark_paper(True)
            for angle in SERVO_ANGLES:
                self.step(f'Servo 1: {angle}°', 'servo1', angle, SERVO_STEP_SECONDS)
            self.step(f'Feeding paper: motor {SPEED:.0%}, 5 seconds', 'motor', SPEED, REVERSE_SECONDS)
            self.step('Paper stopped for camera', 'stop', seconds=0.7, stage='capturing')
            self.check()
            self.stage, self.message = 'capturing', 'Focusing camera on the paper' if focus.status()['supported'] else 'Capturing paper image'
            device = 'camera'
            with camera_lock:
                focus.prepare_capture(camera, self.cancelled)
                self.check()
                self.message = 'Capturing paper image'
                image = camera.capture_array('main')
            if image is None or not getattr(image, 'size', 0):
                raise RuntimeError('Camera returned an empty image')
            with camera_lock:
                if preview:
                    preview.detach()
                camera.stop()
                camera.close()
                camera = None
            self.check()
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            if health:
                health.mark('camera', 'captured', 'Camera frame captured successfully')
            device = None
            capture_path = self.folder / f'{uuid.uuid4().hex}.png'
            ok, png = cv2.imencode('.png', gray)
            if not ok:
                raise RuntimeError('Camera image could not be encoded.')
            with capture_path.open('xb') as file:
                os.chmod(capture_path, 0o600)
                file.write(png.tobytes())
            self.stage, self.message = 'ocr', 'Reading QR and text; checking official records'
            expected_pages = []
            def lookup(token, number):
                found = client.lookup(token, number)
                expected_pages.append(found.get('page_count', 1))
                return found
            documents = process_stack([CapturedPage(1, gray, source=str(capture_path))], lookup)
            self.check()
            if len(documents) != 1:
                raise ValueError('Expected exactly one document. Paper is held for checking.')
            document = documents[0]
            document.payload['position'] = session.get('next_position', 1)
            self.stage, self.message = 'validating', 'Comparing scanned information with official records'
            self.result = client.submit(session_id, document)
            self.progress = 85
            self.check()
            footer = document.payload.get('pages', [{}])[0].get('footer') or {}
            if any(count != 1 for count in expected_pages) or footer.get('pages', 1) != 1:
                self.stage, self.message = 'held', 'Multi-page document: held. This mechanism currently captures one sheet per cycle.'
                return
            result = self.result.get('result')
            if result not in ('VALID', 'INVALID'):
                self.progress = 100
                self.stage, self.message = 'held', 'NEEDS REVIEW: paper held. Check the result and remove the sheet.'
                return
            # Recheck the session before routing; never eject after an end/stop/new session.
            current = client.session()
            self.check()
            if current.get('session_id') != session_id:
                raise ValueError('Session changed. Paper is held.')
            angle = TRAY_UP_ANGLE if result == 'VALID' else TRAY_DOWN_ANGLE
            self.step(f'Selecting {result} tray: {angle}°', 'servo3', angle, 0.7, stage='sorting')
            self.step(f'Ejecting to {result}: servo 2, {SECOND_SERVO_SECONDS:g} seconds', 'servo2',
                      SECOND_SERVO_SPEED, SECOND_SERVO_SECONDS, stage='sorting')
            self.step('Stopping eject servo', 'servo2', SECOND_SERVO_NEUTRAL, 0.3, stage='sorting')
            self.step('Eject servo released', 'release_servo2', stage='sorting')
            with self.machine.lock:
                self.check()
                self.mark_paper(False)
                self.stage, self.message = 'complete', f'{result}: ejection command completed. Check the tray.'
        except (Cancelled, FocusCancelled):
            self.stage, self.message = 'held' if self.held else 'idle', 'Stopped. Check and remove any paper.'
        except FocusError as error:
            log.warning('Scan held because autofocus did not finish: %s', error)
            self.stage = 'held' if self.held else 'error'
            self.error = str(error)
            self.message = self.error
        except HardwareFault as error:
            log.exception('Device command failed')
            self.stage = 'held' if self.held else 'error'
            self.error = str(error)
            self.message = self.error
        except ImportError as error:
            log.exception('Camera/OCR software could not load')
            self.stage = 'held' if self.held else 'error'
            if (getattr(error, 'name', None) or '').split('.')[0] in ('picamera2', 'libcamera'):
                self.error = ('Raspberry Pi camera software is unavailable. Picamera2 and '
                              'libcamera must be accessible to the station Python environment.')
            else:
                self.error = 'Camera/OCR software could not load. Check the station Python setup.'
            self.message = self.error
            if health and (getattr(error, 'name', None) or '').split('.')[0] in ('picamera2', 'libcamera'):
                health.fail('camera', self.error)
        except Exception:
            # Full traceback stays local; API responses must never expose station keys.
            log.exception('Camera workflow failed')
            self.stage, self.message = 'held' if self.held else 'error', 'Camera/OCR/validation failed. Check local server.log; paper was not automatically retried.'
            self.error = 'Camera or document processing failed. Check the local station log for the cause.'
            if device == 'camera':
                self.error = 'Raspberry Pi camera capture failed. Check its connection and close other camera programs.'
                if health:
                    health.fail('camera', self.error)
            elif device == 'gpio' and health:
                health.fail('gpio')
            self.message = self.error
        finally:
            try:
                if camera is not None:
                    with camera_lock:
                        if preview:
                            preview.detach()
                        camera.close()
            finally:
                if preview:
                    preview.end_scan()
                if client is not None:
                    client.http.close()
                with self.machine.lock:
                    try:
                        if self.machine.hardware:
                            self.machine.hardware.stop_all()
                    finally:
                        self.machine.lease_active = False
