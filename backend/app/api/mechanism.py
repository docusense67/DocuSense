"""Authenticated gateway to the camera station's loopback hardware bridge."""
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
from app.models.enums import ScanMode
from app.services import audit

router = APIRouter(prefix='/mechanism', tags=['mechanism'])
log = logging.getLogger(__name__)


class Command(BaseModel):
    action: Literal['scan', 'stop', 'heartbeat', 'acknowledge', 'tray', 'connect',
                    'disconnect', 'test-motor', 'test-servo1', 'test-servo2', 'test-servo3']
    session_id: int | None = Field(None, ge=1)
    angle: float | None = Field(None, ge=0, le=180, allow_inf_nan=False)


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


@router.get('/status')
def status(user: User = Depends(require_validator)):
    settings = get_settings()
    if not settings.mechanism_url:
        return {'enabled': False}
    observed = bridge('GET', '/integration/status')
    # Ignore retired device-check payloads from bridges awaiting restart.
    observed.pop('health', None)
    return {'enabled': True, 'station_id': settings.mechanism_station_id, **observed}


@router.get('/camera/frame')
def camera_frame(user: User = Depends(require_validator)):
    settings = get_settings()
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
