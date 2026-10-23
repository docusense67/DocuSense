"""Station side of the cloud mechanism relay (docs/cloud-motor-control.md).

The Pi's relay process calls these with the station key (X-Station-Key). It connects out to the
API; the API never connects to the Pi. Only the configured camera station
(ADGVS_MECHANISM_STATION_ID) gets past relay_station, and it only ever receives its own
commands: an action name and the backend's fixed parameters, never student data.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_station
from app.config import get_settings
from app.db import get_db
from app.models import MechanismCommand, ScanStation
from app.services import mechanism_relay as relay
from app.timeutil import utcnow

router = APIRouter(prefix='/station/mechanism', tags=['mechanism-relay'])


def relay_station(station: ScanStation = Depends(get_station)) -> ScanStation:
    settings = get_settings()
    if settings.mechanism_mode != 'relay':
        raise HTTPException(409, 'The cloud mechanism relay is off on this server '
                            '(ADGVS_MECHANISM_MODE=loopback).')
    if station.id != settings.mechanism_station_id:
        raise HTTPException(403, 'This station is not the configured camera station.')
    return station


class ResultIn(BaseModel):
    ok: bool
    error: str | None = Field(None, max_length=2000)
    # The bridge status right after the command (saves a separate state report).
    status: dict | None = None


class StateIn(BaseModel):
    state: dict
    remote_allowed: bool = False


@router.get('/commands')
def poll_commands(wait: float = Query(0, ge=0, le=relay.MAX_POLL_WAIT_SECONDS),
                  station: ScanStation = Depends(relay_station), db: Session = Depends(get_db)):
    """Long-poll this station's commands. Also says whether the controlling operator's
    heartbeat is fresh (operator_fresh_for, seconds) and whether anyone watches the preview."""
    station_id = station.id
    db.commit()  # hand the connection back before waiting
    relay.maybe_prune(db, utcnow())
    return relay.poll_commands(db, station_id, wait)


@router.post('/commands/{command_id}/result')
def post_result(command_id: int, body: ResultIn, station: ScanStation = Depends(relay_station),
                db: Session = Depends(get_db)):
    command = db.get(MechanismCommand, command_id)
    if command is None or command.station_id != station.id:
        raise HTTPException(404, 'MechanismCommand not found')
    if command.delivered_at is None or command.completed_at is not None:
        raise HTTPException(409, 'This command is not waiting for a result.')
    now = utcnow()
    command.completed_at = now
    command.result = {'ok': body.ok, 'error': None if body.ok else relay.clean_error(body.error)}
    row = relay.state_row_or_new(db, station.id)
    if body.status is not None:
        try:
            row.state = relay.clean_state(body.status)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        row.updated_at = now
    payload = relay.control_payload(row, now)
    db.commit()
    relay.results_signal.notify(station.id)
    return payload


@router.post('/state')
def post_state(body: StateIn, station: ScanStation = Depends(relay_station),
               db: Session = Depends(get_db)):
    try:
        state = relay.clean_state(body.state)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    now = utcnow()
    row = relay.state_row_or_new(db, station.id)
    row.state, row.updated_at, row.remote_allowed = state, now, body.remote_allowed
    payload = relay.control_payload(row, now)
    db.commit()
    return payload


@router.post('/frame')
async def post_frame(request: Request, station: ScanStation = Depends(relay_station)):
    """One JPEG preview frame (raw body, at most MAX_FRAME_BYTES), kept in memory only."""
    declared = request.headers.get('content-length')
    if declared is not None and (not declared.isdigit()
                                 or int(declared) > relay.MAX_FRAME_BYTES):
        raise HTTPException(413, f'Frame over {relay.MAX_FRAME_BYTES} bytes')
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > relay.MAX_FRAME_BYTES:
            raise HTTPException(413, f'Frame over {relay.MAX_FRAME_BYTES} bytes')
    problem = relay.frame_problem(bytes(data))
    if problem:
        raise HTTPException(422, problem)
    relay.put_frame(station.id, bytes(data), utcnow())
    return {'ok': True}
