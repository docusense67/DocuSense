"""Authenticated gateway to the camera station's mechanism (feeder, motor, servos, camera).

ADGVS_MECHANISM_MODE=loopback (default): the API runs on the Pi and calls the loopback bridge.
ADGVS_MECHANISM_MODE=relay (cloud): the API never connects to the Pi. Commands wait in the
database for the station's relay process; status and preview come from what it reported
(services/mechanism_relay.py, docs/cloud-motor-control.md). The three routes answer the same
way in both modes.
"""
import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_or_404, require_validator
from app.config import get_settings
from app.db import get_db
from app.models import ScanSession, ScanStation, User
from app.models.enums import MechanismAction, ScanMode
from app.services import audit
from app.services import mechanism_relay as relay
from app.timeutil import utcnow

router = APIRouter(prefix='/mechanism', tags=['mechanism'])
log = logging.getLogger(__name__)


class Command(BaseModel):
    action: Literal['scan', 'stop', 'heartbeat', 'acknowledge', 'tray', 'connect',
                    'disconnect', 'test-motor', 'test-servo1', 'test-servo2', 'test-servo3']
    session_id: int | None = Field(None, ge=1)
    angle: float | None = Field(None, ge=0, le=180, allow_inf_nan=False)


def relay_mode() -> bool:
    return get_settings().mechanism_mode == 'relay'


def bridge(method, path, body=None):
    settings = get_settings()
    if not settings.mechanism_url:
        raise HTTPException(409, 'Camera mechanism is not configured on this server.')
    try:
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            response = client.request(method, settings.mechanism_url.rstrip('/') + path,
                                      headers={
                                          'X-ADGVS-Bridge-Key':
                                              settings.mechanism_key.get_secret_value()},
                                      json=body)
            if response.status_code in (400, 503):
                raise HTTPException(409 if response.status_code == 400 else 503,
                    response.json().get('error', 'Mechanism is busy or unavailable.'))
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError):
        log.exception('Local mechanism bridge unavailable')
        raise HTTPException(503, 'Camera station unavailable. '
                            'Start the local mechanism service.') from None


RELAY_OFFLINE = ('Camera station is not connected to the server. '
                 'Start the mechanism relay on the station.')


def relay_status(row, now) -> dict:
    """Status as the station's relay reported it; 503 (like an unreachable bridge) when stale."""
    if row is None or relay.state_is_stale(row.updated_at, now):
        raise HTTPException(503, RELAY_OFFLINE)
    observed = dict(row.state or {})
    observed.pop('health', None)
    return {**observed, 'enabled': True, 'station_id': row.station_id, 'relay': True,
            'remote_control': row.remote_allowed}


@router.get('/status')
def status(user: User = Depends(require_validator), db: Session = Depends(get_db)):
    settings = get_settings()
    if relay_mode():
        row = relay.state_row(db, settings.mechanism_station_id)
        db.commit()
        return relay_status(row, utcnow())
    if not settings.mechanism_url:
        return {'enabled': False}
    observed = bridge('GET', '/integration/status')
    # Ignore retired device-check payloads from bridges awaiting restart.
    observed.pop('health', None)
    return {'enabled': True, 'station_id': settings.mechanism_station_id, **observed}


def relay_camera_frame(db: Session, station_id: int) -> Response:
    now = utcnow()
    row = relay.state_row(db, station_id)  # no row lock: the browser asks several times a second
    if row is None or relay.state_is_stale(row.updated_at, now):
        db.commit()
        raise HTTPException(503, 'Camera preview unavailable. '
                            'The camera station is not connected to the server.')
    # Tell the relay someone is watching (it then sends about one frame per second). Written at
    # most every VIEWER_TOUCH; a viewer appearing wakes the relay's long-poll at once.
    if relay.fresh_for(row.viewer_seen_at, now, relay.VIEWER_FRESH - relay.VIEWER_TOUCH) <= 0:
        appeared = relay.fresh_for(row.viewer_seen_at, now, relay.VIEWER_FRESH) <= 0
        row.viewer_seen_at = now
        db.commit()
        if appeared:
            relay.commands_signal.notify(station_id)
    else:
        db.commit()
    frame = relay.latest_frame(station_id, now)
    if frame is None:
        raise HTTPException(503, 'Waiting for camera preview.')
    return Response(frame[0], media_type='image/jpeg', headers={
        'Cache-Control': 'no-store', 'X-Frame-Time': frame[1].isoformat()})


@router.get('/camera/frame')
def camera_frame(user: User = Depends(require_validator), db: Session = Depends(get_db)):
    settings = get_settings()
    if relay_mode():
        return relay_camera_frame(db, settings.mechanism_station_id)
    if not settings.mechanism_url:
        raise HTTPException(409, 'Camera mechanism is not configured.')
    try:
        with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
            response = client.get(settings.mechanism_url.rstrip('/') + '/integration/camera/frame',
                headers={'X-ADGVS-Bridge-Key': settings.mechanism_key.get_secret_value()})
            if response.status_code == 503:
                raise HTTPException(503, response.json().get(
                    'error', 'Waiting for camera preview.'))
            response.raise_for_status()
            if (response.headers.get('content-type') != 'image/jpeg'
                    or len(response.content) > 2_000_000):
                raise ValueError('Invalid preview frame')
            return Response(response.content, media_type='image/jpeg', headers={
                'Cache-Control': 'no-store',
                'X-Frame-Time': response.headers.get('X-Frame-Time', '')})
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'Camera preview unavailable. '
                            'Check the local camera service.') from None


def relay_command(db: Session, user: User, body: Command, data: dict, station_id: int,
                  request: Request) -> dict:
    """Queue the command for the station's relay and wait briefly for its result.

    Validation (session, angles, active station) already happened in command(). Here: one
    controller per station, the station's local opt-in, and the controller's heartbeat that
    lets the relay keep the bridge's dead-man alive. stop and disconnect are accepted from any
    validation operator, even while the relay is offline (they never expire and are delivered
    first)."""
    now = utcnow()
    action = None if body.action == 'heartbeat' else MechanismAction(data['action'])
    safe = action in relay.NEVER_EXPIRE
    row = relay.state_row_or_new(db, station_id)
    if not safe:
        refusal = relay.control_refusal(user.id, relay.open_session_operator(db, station_id),
                                        row.controller_id, row.controller_seen_at, now)
        if refusal:
            db.rollback()
            raise HTTPException(409, refusal)
        if relay.state_is_stale(row.updated_at, now):
            db.rollback()
            raise HTTPException(503, RELAY_OFFLINE)
        if not row.remote_allowed:
            db.rollback()
            raise HTTPException(409, 'Remote control is switched off on the station. Enable it '
                                'there (relay --allow-remote) to move the mechanism.')
        row.controller_id, row.controller_seen_at, row.heartbeat_at = user.id, now, now
    if body.action == 'heartbeat':
        db.commit()
        return relay_status(row, now)
    if action == MechanismAction.DISCONNECT and row.controller_id == user.id:
        row.controller_id = row.heartbeat_at = None  # the controller hands back control
    # Record the request before queueing it; this is not a claim that anything moved.
    audit.record(db, 'mechanism.requested', user_id=user.id, entity_type='scan_session',
                 entity_id=body.session_id, new_value=body.model_dump(exclude_none=True),
                 ip_address=client_ip(request))
    items = [(action, {k: v for k, v in data.items() if k != 'action'})]
    if body.action == 'tray':
        items.insert(0, (MechanismAction.CONNECT, {}))
    ids = [c.id for c in relay.enqueue(db, station_id, items, user.id, now)]
    db.commit()
    relay.commands_signal.notify(station_id)
    for done in relay.wait_for_results(db, ids, station_id):
        result = done.result or {}
        if done.completed_at is None or result.get('error') == 'expired':
            raise HTTPException(503, 'The camera station did not confirm the command in time. '
                                'Movement commands expire after a few seconds and never run '
                                'late.')
        if result.get('error') == 'superseded':
            raise HTTPException(409, 'Stopped: a stop was requested before this command ran.')
        if not result.get('ok'):
            raise HTTPException(409, result.get('error') or 'The station refused the command.')
    row = relay.state_row(db, station_id)
    db.commit()
    return relay_status(row, utcnow())


@router.post('/command')
def command(body: Command, request: Request, user: User = Depends(require_validator),
            db: Session = Depends(get_db)):
    settings = get_settings()
    data = body.model_dump(exclude_none=True)
    if body.action == 'scan':
        if body.session_id is None:
            raise HTTPException(422, 'Open a single-document session first.')
        session = get_or_404(db, ScanSession, body.session_id)
        if (session.operator_id != user.id or session.ended_at is not None
                or session.station_id != settings.mechanism_station_id
                or session.mode != ScanMode.SINGLE):
            raise HTTPException(409, 'Use your open single-document session '
                                'for the camera station.')
        station = get_or_404(db, ScanStation, session.station_id)
        if not station.is_active:
            raise HTTPException(409, 'Camera station is inactive.')
        # Fixed routing also supports an older bridge awaiting restart. Never use
        # client-supplied calibration values or require operator confirmation.
        data = {'action': 'scan', 'session_id': body.session_id,
                'valid_angle': 180, 'invalid_angle': 90, 'calibrated': True}
    elif body.action in ('tray', 'test-servo3'):
        if body.angle is None:
            raise HTTPException(422, 'Enter a tray angle.')
        data = {'action': 'up' if body.action == 'tray' else 'test-servo3', 'angle': body.angle}
    else:
        data = {'action': body.action}
    if body.action in ('connect', 'tray', 'test-motor', 'test-servo1',
                       'test-servo2', 'test-servo3'):
        if not settings.mechanism_station_id:
            raise HTTPException(409, 'Configure the local camera station first.')
        station = get_or_404(db, ScanStation, settings.mechanism_station_id)
        if not station.is_active:
            raise HTTPException(409, 'Camera station is inactive.')
    if relay_mode():
        return relay_command(db, user, body, data, settings.mechanism_station_id, request)
    if body.action != 'heartbeat':
        # Record the request before sending a physical command; this is not a claim
        # that the motor moved or that ejection succeeded.
        audit.record(db, 'mechanism.requested', user_id=user.id, entity_type='scan_session',
                     entity_id=body.session_id, new_value=body.model_dump(exclude_none=True),
                     ip_address=client_ip(request))
        db.commit()
    if body.action == 'tray':
        bridge('POST', '/integration/action', {'action': 'connect'})
    return bridge('POST', '/integration/action', data)
