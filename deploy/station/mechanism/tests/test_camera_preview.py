"""Preview ownership and freshness checks without physical camera access."""
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera_preview import CameraPreview
from device_health import DeviceHealth


class CameraPreviewTests(unittest.TestCase):
    def test_frames_share_scan_camera_and_expire_without_viewer(self):
        idle, scan = Mock(camera_controls={}), Mock(camera_controls={})
        for camera in (idle, scan):
            camera.capture_array.return_value = np.zeros((480, 640, 3), dtype=np.uint8)
        factory = Mock(return_value=idle)
        preview = CameraPreview(SimpleNamespace(simulate=False, health=DeviceHealth()))
        with patch.dict(sys.modules, {'picamera2': SimpleNamespace(Picamera2=factory)}):
            try:
                self.assertIsNone(preview.request_frame()[0])
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if preview.request_frame()[0] is not None:
                        break
                    time.sleep(0.03)
                frame, timestamp, error = preview.request_frame()
                self.assertTrue(frame.startswith(b'\xff\xd8'))
                self.assertTrue(timestamp)
                self.assertIsNone(error)
                preview.begin_scan()
                idle.close.assert_called_once()
                preview.attach(scan)
                preview.wake.set()
                deadline = time.monotonic() + 2
                while not scan.capture_array.called and time.monotonic() < deadline:
                    time.sleep(0.03)
                self.assertTrue(scan.capture_array.called)
                self.assertEqual(factory.call_count, 1)
                with preview.io:
                    preview.detach()
                    preview.last_request = time.monotonic() - 4
                preview.wake.set()
                time.sleep(0.3)
                self.assertIsNone(preview.frame)
                scan.close.assert_not_called()
            finally:
                preview.close()
                if preview.worker:
                    preview.worker.join(timeout=3)

    def test_simulation_never_opens_a_camera(self):
        preview = CameraPreview(SimpleNamespace(simulate=True, health=DeviceHealth()))
        frame, _, message = preview.request_frame()
        self.assertIsNone(frame)
        self.assertIn('simulation', message)
        self.assertIsNone(preview.worker)
