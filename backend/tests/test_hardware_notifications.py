"""Retired hardware alerts stay hidden; authenticated camera preview stays available."""
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog
from tests.station_test_base import StationTestBase


class HardwareNotificationTests(StationTestBase):
    def configure(self):
        settings = get_settings()
        for name, value in (('mechanism_url', 'http://127.0.0.1:8765'),
                            ('mechanism_station_id', self.station.id)):
            mocked = patch.object(settings, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.users['staff'].can_validate = True
        self.db.commit()
        self.login('staff')

    def fault_payload(self):
        return {'health': {'devices': [], 'events': [{
            'id': 'a' * 32, 'device': 'servo1', 'kind': 'fault', 'pins': [12],
            'message': 'Feeder servo access failed on GPIO 12; check wiring and GPIO access.',
        }]}}

    async def test_status_ignores_retired_device_alerts(self):
        self.configure()
        with patch('app.api.mechanism.bridge', return_value=self.fault_payload()):
            response = await self.client.get('/api/mechanism/status')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn('health', response.json())
        self.assertEqual(list(self.db.scalars(select(AuditLog).where(
            AuditLog.action.in_(('hardware_fault', 'hardware_recovered'))))), [])

    async def test_historical_hardware_alerts_are_hidden_without_polling_bridge(self):
        self.configure()
        from app.services import audit
        for action in ('hardware_fault', 'hardware_recovered'):
            audit.record(self.db, action, entity_type='scan_station', entity_id=self.station.id,
                         new_value={'device': 'servo1', 'label': 'Feeder servo',
                                    'message': 'Old alert'})
        self.db.commit()
        with patch('app.api.mechanism.bridge') as bridge:
            for role in ('staff', 'admin'):
                self.login(role)
                response = await self.client.get('/api/notifications')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertFalse(any('Feeder servo' in item['title'] for item in response.json()))
            bridge.assert_not_called()

    async def test_camera_preview_requires_operator_and_forwards_uncached_jpeg(self):
        self.configure()
        response = Mock(status_code=200, content=b'jpeg-frame', headers={
            'content-type': 'image/jpeg', 'X-Frame-Time': '2026-10-19T01:00:00+00:00'})
        with patch('app.api.mechanism.httpx.Client') as factory:
            client = factory.return_value.__enter__.return_value
            client.get.return_value = response
            preview = await self.client.get('/api/mechanism/camera/frame')
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.content, b'jpeg-frame')
            self.assertEqual(preview.headers['cache-control'], 'no-store')
            self.assertEqual(preview.headers['x-frame-time'], '2026-10-19T01:00:00+00:00')
            self.login('student')
            forbidden = await self.client.get('/api/mechanism/camera/frame')
            self.assertEqual(forbidden.status_code, 403)
            self.assertEqual(client.get.call_count, 1)

    async def test_camera_waiting_state_does_not_return_a_stale_image(self):
        self.configure()
        response = Mock(status_code=503)
        response.json.return_value = {'error': 'Waiting for a new camera frame'}
        with patch('app.api.mechanism.httpx.Client') as factory:
            factory.return_value.__enter__.return_value.get.return_value = response
            preview = await self.client.get('/api/mechanism/camera/frame')
            self.assertEqual(preview.status_code, 503)
            self.assertIn('Waiting for a new camera frame', preview.json()['detail'])


    async def test_manual_commands_require_operator_and_are_audited(self):
        self.configure()
        actions = ('connect', 'test-motor', 'test-servo1', 'test-servo2',
                   'test-servo3', 'disconnect')
        with patch('app.api.mechanism.bridge', return_value={'connected': True}) as bridge:
            for action in actions:
                body = {'action': action}
                if action == 'test-servo3':
                    body['angle'] = 90
                response = await self.client.post('/api/mechanism/command', json=body)
                self.assertEqual(response.status_code, 200, response.text)
                forwarded = bridge.call_args.args[2]
                self.assertEqual(forwarded['action'], action)
            self.assertEqual(bridge.call_count, len(actions))
            records = list(self.db.scalars(select(AuditLog).where(
                AuditLog.action == 'mechanism.requested')))
            self.assertEqual({r.new_value['action'] for r in records}, set(actions))
            self.login('student')
            denied = await self.client.post('/api/mechanism/command', json={'action': 'test-motor'})
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(bridge.call_count, len(actions))

    async def test_invalid_angles_and_inactive_station_never_start_manual_movement(self):
        self.configure()
        with patch('app.api.mechanism.bridge', return_value={}) as bridge:
            for angle in (None, -1, 181):
                body = {'action': 'test-servo3'}
                if angle is not None:
                    body['angle'] = angle
                invalid = await self.client.post('/api/mechanism/command', json=body)
                self.assertEqual(invalid.status_code, 422)
            self.station.is_active = False
            self.db.commit()
            rejected = await self.client.post('/api/mechanism/command',
                                              json={'action': 'test-motor'})
            self.assertEqual(rejected.status_code, 409)
            bridge.assert_not_called()
            stopped = await self.client.post('/api/mechanism/command', json={'action': 'stop'})
            self.assertEqual(stopped.status_code, 200)

    async def test_gpio_command_error_is_shown_inline(self):
        self.configure()
        response = Mock(status_code=503)
        response.json.return_value = {'error': 'Hardware unavailable: GPIO busy'}
        with patch('app.api.mechanism.httpx.Client') as factory:
            factory.return_value.__enter__.return_value.request.return_value = response
            result = await self.client.post('/api/mechanism/command', json={'action': 'connect'})
            self.assertEqual(result.status_code, 503)
            self.assertIn('GPIO busy', result.json()['detail'])

    async def test_scan_without_calibration_uses_station_fixed_positions(self):
        from app.models import ScanSession
        from app.models.enums import ScanMode

        self.configure()
        session = ScanSession(mode=ScanMode.SINGLE, operator_id=self.users['staff'].id,
                              station_id=self.station.id)
        self.db.add(session)
        self.db.commit()
        with patch('app.api.mechanism.bridge', return_value={}) as bridge:
            for extra in ({}, {'valid_angle': 20, 'invalid_angle': 30, 'calibrated': False}):
                response = await self.client.post('/api/mechanism/command', json={
                    'action': 'scan', 'session_id': session.id, **extra})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(bridge.call_args.args[2],
                                 {'action': 'scan', 'session_id': session.id,
                                  'valid_angle': 180, 'invalid_angle': 90, 'calibrated': True})
            self.login('teacher')
            denied = await self.client.post('/api/mechanism/command',
                json={'action': 'scan', 'session_id': session.id})
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(bridge.call_count, 2)


if __name__ == '__main__':
    unittest.main()
