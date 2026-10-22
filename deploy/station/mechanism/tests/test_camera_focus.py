"""Autofocus capability checks, bounded waiting and cancellation without hardware."""
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera_focus import CameraFocus, FocusCancelled, FocusError

CONTROLS = SimpleNamespace(AfModeEnum=SimpleNamespace(Continuous='continuous'),
                           AfRangeEnum=SimpleNamespace(Full='full'))


class CameraFocusTests(unittest.TestCase):
    def make_supported(self):
        camera = Mock(camera_controls={'AfMode': None, 'AfTrigger': None, 'AfRange': None})
        focus = CameraFocus()
        with patch.dict(sys.modules, {'libcamera': SimpleNamespace(controls=CONTROLS)}):
            focus.configure(camera)
        return focus, camera

    def test_fixed_focus_camera_is_reported_without_unsupported_commands(self):
        focus, camera = CameraFocus(), Mock(camera_controls={})
        focus.configure(camera)
        focus.prepare_capture(camera, threading.Event())
        self.assertFalse(focus.status()['supported'])
        self.assertEqual(focus.status()['state'], 'manual')
        camera.set_controls.assert_not_called()
        camera.autofocus_cycle.assert_not_called()

    def test_continuous_preview_and_successful_focus_lock(self):
        focus, camera = self.make_supported()
        camera.set_controls.assert_called_once_with({'AfMode': 'continuous', 'AfRange': 'full'})
        self.assertEqual(focus.status()['state'], 'continuous')
        camera.wait.side_effect = [TimeoutError(), True]
        focus.prepare_capture(camera, threading.Event())
        self.assertEqual(focus.status()['state'], 'focused')
        camera.autofocus_cycle.assert_called_once_with(wait=False)
        self.assertEqual(camera.wait.call_count, 2)
        camera.cancel_all_and_flush.assert_not_called()

    def test_failed_focus_does_not_claim_camera_is_focused(self):
        focus, camera = self.make_supported()
        camera.wait.return_value = False
        with self.assertRaises(FocusError):
            focus.prepare_capture(camera, threading.Event())
        self.assertEqual(focus.status()['state'], 'failed')

    def test_timeout_clears_queued_camera_job(self):
        focus, camera = self.make_supported()
        with patch('camera_focus.monotonic', side_effect=[0, 5]):
            with self.assertRaisesRegex(FocusError, 'timed out'):
                focus.prepare_capture(camera, threading.Event(), timeout=4)
        camera.cancel_all_and_flush.assert_called_once()
        self.assertEqual(focus.status()['state'], 'failed')

    def test_stop_cancels_focus_and_clears_queued_job(self):
        focus, camera = self.make_supported()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(FocusCancelled):
            focus.prepare_capture(camera, stop)
        camera.cancel_all_and_flush.assert_called_once()
        self.assertEqual(focus.status()['state'], 'cancelled')
