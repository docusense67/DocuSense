"""Run the camera workflow with recorded hardware and simulated backend decisions."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scan-agent"))

from workflow import Workflow  # noqa: E402
from device_health import DeviceHealth  # noqa: E402


class SortingTests(unittest.TestCase):
    def run_decision(self, result, interrupt=False, camera_error=False, autofocus=None):
        hardware = Mock()
        machine = SimpleNamespace(lock=threading.RLock(), hardware=hardware,
                                  connect=Mock(), error=None, lease_active=True,
                                  health=DeviceHealth())
        camera = Mock(camera_controls={} if autofocus is None else {'AfMode': None, 'AfTrigger': None})
        camera.wait.return_value = autofocus
        self.camera, self.client = camera, None
        camera.capture_array.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        if camera_error:
            camera.capture_array.side_effect = RuntimeError('Camera disconnected')
        client = Mock()
        self.client = client
        client.session.return_value = {"session_id": 1, "mode": "single", "next_position": 1}
        client.submit.return_value = {"result": result}
        document = SimpleNamespace(payload={"pages": [{"footer": {"pages": 1}}]})
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"ADGVS_CAMERA_FOLDER": folder,
                                         "ADGVS_CAMERA_STATION_KEY": "test-key"}), \
                 patch.dict(sys.modules, {
                     "picamera2": SimpleNamespace(Picamera2=Mock(return_value=camera)),
                     "libcamera": SimpleNamespace(controls=SimpleNamespace(
                         AfModeEnum=SimpleNamespace(Continuous=2))),
                 }), \
                 patch("scan_agent.client.BackendClient", return_value=client), \
                 patch("scan_agent.pipeline.process_stack", return_value=[document]), \
                 patch("pytesseract.get_tesseract_version", return_value="test"):
                workflow = Workflow(machine)
                self.workflow = workflow
                self.progress_events = []
                hardware.apply.side_effect = lambda *args: self.progress_events.append(
                    (workflow.stage, workflow.progress))
                workflow.cancelled = Mock()
                workflow.cancelled.wait.return_value = False
                if interrupt:
                    workflow.cancelled.wait.side_effect = [False, True]
                workflow.cancelled.is_set.return_value = False
                workflow.run(1)
                return workflow.stage, workflow.held, hardware.apply.call_args_list

    def test_autofocus_locks_before_document_capture(self):
        stage, held, _ = self.run_decision('VALID', autofocus=True)
        self.assertEqual(stage, 'complete')
        self.assertFalse(held)
        calls = [call[0] for call in self.camera.method_calls]
        self.assertLess(calls.index('autofocus_cycle'), calls.index('capture_array'))
        self.assertLess(calls.index('wait'), calls.index('capture_array'))

    def test_autofocus_failure_holds_paper_without_capture_or_routing(self):
        stage, held, commands = self.run_decision('VALID', autofocus=False)
        self.assertEqual(stage, 'held')
        self.assertTrue(held)
        self.assertIn('Autofocus could not lock', self.workflow.error)
        self.camera.capture_array.assert_not_called()
        self.client.submit.assert_not_called()
        self.assertFalse(any(call.args[0] in ('servo2', 'servo3') for call in commands))

    def test_valid_uses_valid_tray(self):
        stage, held, commands = self.run_decision("VALID")
        self.assertEqual(stage, "complete")
        self.assertFalse(held)
        self.assertTrue(any(call.args == ("servo3", 180) for call in commands))
        self.assertTrue(any(call.args[0] == "servo2" for call in commands))

    def test_invalid_uses_invalid_tray(self):
        stage, held, commands = self.run_decision("INVALID")
        self.assertEqual(stage, "complete")
        self.assertFalse(held)
        self.assertTrue(any(call.args == ("servo3", 90) for call in commands))

    def test_unresolved_document_is_held_without_ejection(self):
        stage, held, commands = self.run_decision("NEEDS_REVIEW")
        self.assertEqual(stage, "held")
        self.assertTrue(held)
        self.assertFalse(any(call.args[0] in ("servo2", "servo3") for call in commands))

    def test_progress_tracks_feeding_capture_sorting_and_completion(self):
        self.run_decision("VALID")
        self.assertIn(("feeding", 20), self.progress_events)
        self.assertIn(("capturing", 40), self.progress_events)
        self.assertIn(("sorting", 90), self.progress_events)
        self.assertEqual(self.workflow.status()["progress"], 100)

    def test_interrupted_feed_retains_its_actual_progress(self):
        stage, held, commands = self.run_decision("VALID", interrupt=True)
        self.assertEqual(stage, "held")
        self.assertTrue(held)
        self.assertEqual(self.workflow.status()["progress"], 20)
        self.assertIsNone(self.workflow.result)
        self.assertFalse(any(call.args[0] in ("servo2", "servo3") for call in commands))

    def test_missing_camera_module_is_reported_before_feeding(self):
        hardware = Mock()
        machine = SimpleNamespace(lock=threading.RLock(), hardware=hardware,
                                  connect=Mock(), error=None, lease_active=True,
                                  health=DeviceHealth())
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {"ADGVS_CAMERA_FOLDER": folder}), \
             patch.dict(sys.modules, {"picamera2": None}):
            workflow = Workflow(machine)
            workflow.stage = 'preflight'
            with self.assertLogs('workflow', level='ERROR'):
                workflow.run(1)
            self.assertEqual(workflow.stage, 'error')
            self.assertFalse(workflow.held)
            self.assertIn('Raspberry Pi camera software', workflow.status()['error'])
            self.assertEqual(machine.health.events[-1]['device'], 'camera')
            hardware.apply.assert_not_called()

    def test_camera_capture_failure_holds_paper_and_records_camera_alert(self):
        with self.assertLogs('workflow', level='ERROR'):
            stage, held, commands = self.run_decision('VALID', camera_error=True)
        self.assertEqual(stage, 'held')
        self.assertTrue(held)
        self.assertIsNone(self.workflow.result)
        self.assertEqual(self.workflow.machine.health.events[-1]['device'], 'camera')
        self.assertFalse(any(call.args[0] in ('servo2', 'servo3') for call in commands))

    def test_clear_stopped_scan_resets_hold_error_and_cancellation_without_movement(self):
        self.run_decision('VALID', autofocus=False)
        workflow = self.workflow
        workflow.cancelled = threading.Event()
        workflow.cancelled.set()
        workflow.machine.error = 'Browser connection lost. Movement stopped.'
        before = workflow.machine.hardware.apply.call_count
        with tempfile.TemporaryDirectory() as folder:
            workflow.journal = Path(folder) / 'paper-state.json'
            workflow.acknowledge()
        self.assertFalse(workflow.held)
        self.assertFalse(workflow.cancelled.is_set())
        self.assertIsNone(workflow.error)
        self.assertIsNone(workflow.machine.error)
        self.assertEqual(workflow.stage, 'idle')
        self.assertEqual(workflow.machine.hardware.apply.call_count, before)

    def test_clear_stop_is_blocked_until_running_ocr_finishes(self):
        self.run_decision('NEEDS_REVIEW')
        self.workflow.thread = Mock()
        self.workflow.thread.is_alive.return_value = True
        with self.assertRaisesRegex(ValueError, 'Wait for the stopped'):
            self.workflow.acknowledge()
        self.assertTrue(self.workflow.held)

    def test_scan_start_needs_no_calibration_and_still_blocks_uncleared_paper(self):
        machine = SimpleNamespace(simulate=False, controller=None, error=None, lease_active=False)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
                'ADGVS_CAMERA_FOLDER': folder}), patch('workflow.threading.Thread') as thread:
            workflow = Workflow(machine)
            workflow.start({'session_id': 1})
            self.assertEqual(thread.call_args.kwargs['args'], (1,))
            thread.return_value.start.assert_called_once()
            workflow.thread = None
            workflow.held = True
            with self.assertRaisesRegex(ValueError, 'remove held paper'):
                workflow.start({'session_id': 1})


if __name__ == "__main__":
    unittest.main()
