"""Demand-driven JPEG video, sharing the scan camera without opening it twice."""
import logging
import threading
from datetime import UTC, datetime
from time import monotonic

from camera_runtime import enable_system_camera_packages
from camera_focus import CameraFocus

log = logging.getLogger(__name__)


class CameraPreview:
    def __init__(self, machine):
        self.machine = machine
        self.focus = getattr(machine, 'focus', None) or CameraFocus()
        self.io = threading.RLock()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.scan_requested = False
        self.scan_camera = None
        self.idle_camera = None
        self.frame = None
        self.frame_at = None
        self.frame_time = 0
        self.last_request = 0
        self.error = None
        self.worker = None

    def request_frame(self):
        with self.io:
            self.last_request = monotonic()
            if self.machine.simulate:
                return None, None, 'Camera preview is unavailable in simulation.'
            if self.worker is None:
                self.worker = threading.Thread(target=self.run, daemon=True)
                self.worker.start()
            self.wake.set()
            if self.frame is not None and monotonic() - self.frame_time < 2:
                return self.frame, self.frame_at, None
            return None, None, self.error or 'Waiting for a new camera frame…'

    def _close_idle(self):
        camera, self.idle_camera = self.idle_camera, None
        if camera is not None:
            camera.close()

    def begin_scan(self):
        # Called before the workflow opens Picamera2. Never holds the machine lock.
        with self.io:
            self.scan_requested = True
            self.scan_camera = None
            self.frame = None
            self._close_idle()

    def attach(self, camera):
        with self.io:
            self.scan_camera = camera
            self.wake.set()

    def detach(self):
        # Caller closes the scan camera while holding io, preventing capture races.
        self.scan_camera = None
        self.frame = None

    def end_scan(self):
        with self.io:
            self.scan_camera = None
            self.scan_requested = False
            self.wake.set()

    def run(self):
        try:
            enable_system_camera_packages()
            import cv2
            from picamera2 import Picamera2
            while not self.closed.is_set():
                try:
                    with self.io:
                        wanted = monotonic() - self.last_request < 3
                        if not wanted:
                            self._close_idle()
                            self.frame = None
                        else:
                            if not self.scan_requested and self.idle_camera is None:
                                camera = Picamera2()
                                self.idle_camera = camera
                                camera.configure(camera.create_video_configuration(
                                    main={'size': (640, 480), 'format': 'RGB888'}))
                                self.focus.configure(camera)
                                camera.start()
                            camera = self.scan_camera if self.scan_requested else self.idle_camera
                            if camera is not None:
                                array = camera.capture_array('main')
                                if array is None or not getattr(array, 'size', 0):
                                    raise RuntimeError('Empty camera frame')
                                image = cv2.resize(array, (640, 480))
                                ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 75])
                                if not ok:
                                    raise RuntimeError('JPEG encoding failed')
                                self.frame, self.frame_time = encoded.tobytes(), monotonic()
                                self.frame_at = datetime.now(UTC).isoformat()
                                self.error = None
                                self.machine.health.mark('camera', 'captured', 'Live preview frame received')
                    self.wake.wait(0.2)
                    self.wake.clear()
                except Exception:
                    log.exception('Camera preview failed')
                    with self.io:
                        self.frame = None
                        self.error = 'Camera preview unavailable. Check the CSI connection and close other camera programs.'
                        self.machine.health.fail('camera', self.error)
                        try:
                            self._close_idle()
                        except Exception:
                            log.exception('Preview cleanup failed')
                    self.closed.wait(1)
        except Exception:
            log.exception('Camera preview software unavailable')
            with self.io:
                self.error = 'Camera preview software unavailable. Check Picamera2 and libcamera setup.'
                self.machine.health.fail('camera', self.error)
        finally:
            with self.io:
                self._close_idle()
                self.worker = None

    def close(self):
        self.closed.set()
        self.wake.set()
