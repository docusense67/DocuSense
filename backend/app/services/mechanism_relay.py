"""Cloud relay for the Pi camera mechanism (docs/cloud-motor-control.md).

With ADGVS_MECHANISM_MODE=relay the API never connects to the Pi. An operator's command is
stored in mechanism_commands; the station's relay process (deploy/station/mechanism/relay.py)
long-polls it with the station key, runs it through the Pi's loopback bridge and posts the
result. The relay also reports the bridge's state and, while someone watches, preview frames.

Safety rules are the pure functions at the top (unit-tested in tests/test_mechanism_relay.py):

- A command expires COMMAND_TTL after it was requested; an expired command is never delivered.
  stop and disconnect never expire, are delivered first, and drop every older movement command
  still waiting (the operator's last word was "stop").
- One controller per station: the operator of the station's open scan session, otherwise the
  operator who last sent a command or heartbeat within CONTROL_LEASE. Anyone with the
  validation permission may send stop or disconnect.
- Dead-man: the relay forwards heartbeats to the bridge only while the controller's own
  heartbeat is younger than OPERATOR_FRESH (sent to the relay as `operator_fresh_for`). If
  the browser or the relay's connection goes away, the heartbeats stop and the bridge stops the
  outputs by itself after its own 4-second timeout.

Long-polls never hold a database connection while they wait: every check commits (which hands
the connection back to the pool) before waiting on an in-process signal, with a short timeout.
"""

import json
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import MechanismCommand, MechanismStationState, ScanSession
from app.models.enums import MechanismAction
from app.timeutil import as_utc, utcnow

COMMAND_TTL = timedelta(seconds=5)
NEVER_EXPIRE = frozenset({MechanismAction.STOP, MechanismAction.DISCONNECT})
OPERATOR_FRESH = timedelta(seconds=3)  # controller heartbeat age the relay may act on
CONTROL_LEASE = timedelta(seconds=30)  # control without an open session lapses after this
VIEWER_FRESH = timedelta(seconds=5)  # a preview request keeps frames coming this long
VIEWER_TOUCH = timedelta(seconds=2)  # write viewer_seen_at at most this often
STATE_STALE = timedelta(seconds=15)  # no state report for this long: relay offline
FRAME_STALE = timedelta(seconds=5)
MAX_FRAME_BYTES = 200_000
MAX_STATE_BYTES = 16_000
MAX_ERROR_CHARS = 300
PRUNE_AFTER = timedelta(days=1)
PRUNE_EVERY_SECONDS = 3600.0
RESULT_WAIT_SECONDS = 7.0  # command TTL plus the relay's round trip
MAX_POLL_WAIT_SECONDS = 25
CHECK_EVERY_SECONDS = 1.0  # fallback re-check while waiting (several API workers)

JPEG_MAGIC = b"\xff\xd8\xff"
# Keys the API sets itself; a station report never overrides them.
RESERVED_STATE_KEYS = frozenset({"enabled", "station_id", "relay", "remote_control", "health"})


# ---- pure rules ------------------------------------------------------------------------------

def command_expiry(action: MechanismAction, now: datetime) -> datetime | None:
    return None if action in NEVER_EXPIRE else now + COMMAND_TTL


def is_expired(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and now >= as_utc(expires_at)


def expires_in(expires_at: datetime | None, now: datetime) -> float | None:
    """Seconds left (None: never expires). The relay counts them on its own clock from when it
    sent the request, so clock differences between Render and the Pi never extend a command."""
    if expires_at is None:
        return None
    return max(0.0, round((as_utc(expires_at) - now).total_seconds(), 3))


def plan_delivery(pending: list, now: datetime) -> tuple[list, list[tuple[Any, str]]]:
    """Split one station's waiting commands into (deliver in this order, dropped with reason).

    `pending` items need .id, .action and .expires_at. stop/disconnect come first; a movement
    command requested before the newest stop/disconnect is dropped as "superseded"; an expired
    one as "expired"."""
    last_stop = max((c.id for c in pending if c.action in NEVER_EXPIRE), default=0)
    deliver, dropped = [], []
    for command in sorted(pending, key=lambda c: c.id):
        if command.action not in NEVER_EXPIRE and command.id < last_stop:
            dropped.append((command, "superseded"))
        elif command.action not in NEVER_EXPIRE and is_expired(command.expires_at, now):
            dropped.append((command, "expired"))
        else:
            deliver.append(command)
    deliver.sort(key=lambda c: (c.action not in NEVER_EXPIRE, c.id))
    return deliver, dropped


def fresh_for(seen_at: datetime | None, now: datetime, window: timedelta) -> float:
    """Seconds until `seen_at` is older than `window` (0 when never seen or already old)."""
    if seen_at is None:
        return 0.0
    left = (as_utc(seen_at) + window - now).total_seconds()
    return round(max(0.0, min(left, window.total_seconds())), 3)


def control_refusal(user_id: int, session_operator_id: int | None, controller_id: int | None,
                    controller_seen_at: datetime | None, now: datetime) -> str | None:
    """Why this operator may not move the station now, or None if they may (one controller)."""
    if session_operator_id is not None:
        if session_operator_id != user_id:
            return "Another operator's scan session is open on this camera station."
        return None
    if (controller_id is not None and controller_id != user_id
            and fresh_for(controller_seen_at, now, CONTROL_LEASE) > 0):
        return "Another operator is controlling this camera station. Try again shortly."
    return None


def state_is_stale(updated_at: datetime | None, now: datetime) -> bool:
    return updated_at is None or now - as_utc(updated_at) > STATE_STALE


def frame_problem(data: bytes) -> str | None:
    if not data:
        return "Empty frame"
    if len(data) > MAX_FRAME_BYTES:
        return f"Frame over {MAX_FRAME_BYTES} bytes"
    if not data.startswith(JPEG_MAGIC):
        return "Frame is not a JPEG image"
    return None


def clean_state(state: dict) -> dict:
    """The bridge status as stored: size-capped JSON object, API-owned keys removed."""
    cleaned = {k: v for k, v in state.items() if k not in RESERVED_STATE_KEYS}
    if len(json.dumps(cleaned, allow_nan=False)) > MAX_STATE_BYTES:
        raise ValueError(f"State report over {MAX_STATE_BYTES} bytes")
    return cleaned


def clean_error(error: str | None) -> str | None:
    return None if error is None else error.strip()[:MAX_ERROR_CHARS] or None


# ---- in-process wake-ups ---------------------------------------------------------------------

class Signal:
    """Per-station generation counter: wait() returns early once notify() was called after
    generation() was read, so no wake-up is lost. Waits also time out, because with several
    API workers a notify happens in another process (the database is the source of truth)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._gen: dict[int, int] = defaultdict(int)

    def generation(self, key: int) -> int:
        with self._cond:
            return self._gen[key]

    def notify(self, key: int) -> None:
        with self._cond:
            self._gen[key] += 1
            self._cond.notify_all()

    def wait(self, key: int, seen: int, timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self._gen[key] != seen, timeout=max(0.0, timeout))


commands_signal = Signal()  # a command was queued, or a viewer appeared
results_signal = Signal()  # the relay reported a result


# ---- preview frames (memory only) ------------------------------------------------------------
# Frames are not written to the database (about 1 per second while someone watches would churn
# the free Supabase tier). Render runs one API worker, so the worker that receives a frame also
# serves it; with more workers the preview may show "waiting" until the next frame.

_frames: dict[int, tuple[bytes, datetime]] = {}


def put_frame(station_id: int, data: bytes, at: datetime) -> None:
    _frames[station_id] = (data, at)


def latest_frame(station_id: int, now: datetime) -> tuple[bytes, datetime] | None:
    frame = _frames.get(station_id)
    if frame is None or now - frame[1] > FRAME_STALE:
        return None
    return frame


# ---- database helpers ------------------------------------------------------------------------

def state_row(db: Session, station_id: int, *, lock: bool = False) -> MechanismStationState | None:
    # populate_existing: another request (the relay) changed it since this session last looked.
    query = (select(MechanismStationState).where(MechanismStationState.station_id == station_id)
             .execution_options(populate_existing=True))
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def state_row_or_new(db: Session, station_id: int) -> MechanismStationState:
    row = state_row(db, station_id, lock=True)
    if row is None:
        row = MechanismStationState(station_id=station_id, remote_allowed=False)
        db.add(row)
    return row


def open_session_operator(db: Session, station_id: int) -> int | None:
    return db.scalar(select(ScanSession.operator_id)
                     .where(ScanSession.station_id == station_id, ScanSession.ended_at.is_(None))
                     .order_by(ScanSession.id.desc()).limit(1))


def control_payload(row: MechanismStationState | None, now: datetime) -> dict:
    """What the relay needs besides commands: may it keep the dead-man alive, and is anyone
    watching the preview. Durations, not timestamps, so the Pi's clock does not matter."""
    if row is None:
        return {"operator_fresh_for": 0.0, "viewer_active": False}
    return {"operator_fresh_for": fresh_for(row.heartbeat_at, now, OPERATOR_FRESH),
            "viewer_active": fresh_for(row.viewer_seen_at, now, VIEWER_FRESH) > 0}


def enqueue(db: Session, station_id: int, items: list[tuple[MechanismAction, dict]],
            user_id: int, now: datetime) -> list[MechanismCommand]:
    commands = [MechanismCommand(station_id=station_id, action=action, params=params,
                                 requested_by=user_id, created_at=now,
                                 expires_at=command_expiry(action, now))
                for action, params in items]
    db.add_all(commands)
    db.flush()
    return commands


def take_commands(db: Session, station_id: int, now: datetime) -> list[dict]:
    """Deliver this station's waiting commands (marks them delivered) and drop the expired and
    superseded ones. Commits."""
    pending = list(db.scalars(
        select(MechanismCommand)
        .where(MechanismCommand.station_id == station_id,
               MechanismCommand.delivered_at.is_(None),
               MechanismCommand.completed_at.is_(None))
        .order_by(MechanismCommand.id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)))
    deliver, dropped = plan_delivery(pending, now)
    for command, reason in dropped:
        command.completed_at = now
        command.result = {"ok": False, "error": reason}
    for command in deliver:
        command.delivered_at = now
    out = [{"id": c.id, "action": str(c.action), "params": c.params or {},
            "expires_in": expires_in(c.expires_at, now)} for c in deliver]
    db.commit()
    if dropped:
        results_signal.notify(station_id)
    return out


def poll_commands(db: Session, station_id: int, wait_seconds: float) -> dict:
    """Long-poll: return as soon as there are commands (or something changed), else after
    `wait_seconds`. The session is committed before each wait, so no connection is held.

    `held_for` is how long the API held the poll: the relay counts expiry and operator freshness
    from (its send time + held_for), not from the send time, else a command that arrived late in
    a long poll would already look expired there."""
    started = monotonic()
    deadline = started + max(0.0, min(wait_seconds, MAX_POLL_WAIT_SECONDS))
    while True:
        seen = commands_signal.generation(station_id)
        commands = take_commands(db, station_id, utcnow())
        remaining = deadline - monotonic()
        woke = False
        if not commands and remaining > 0:
            woke = commands_signal.wait(station_id, seen, min(CHECK_EVERY_SECONDS, remaining))
            if not woke:
                continue
            commands = take_commands(db, station_id, utcnow())
        payload = control_payload(state_row(db, station_id), utcnow())
        db.commit()
        return {"commands": commands, **payload, "held_for": round(monotonic() - started, 3)}


def wait_for_results(db: Session, ids: list[int], station_id: int,
                     timeout: float | None = None) -> list[MechanismCommand]:
    """Wait (without holding a connection) until the relay reported every command in `ids`,
    or until the timeout; returns the commands as last read."""
    deadline = monotonic() + (RESULT_WAIT_SECONDS if timeout is None else timeout)
    while True:
        seen = results_signal.generation(station_id)
        commands = list(db.scalars(select(MechanismCommand)
                                   .where(MechanismCommand.id.in_(ids))
                                   .order_by(MechanismCommand.id)
                                   .execution_options(populate_existing=True)))
        db.commit()
        remaining = deadline - monotonic()
        if all(c.completed_at is not None for c in commands) or remaining <= 0:
            return commands
        results_signal.wait(station_id, seen, min(CHECK_EVERY_SECONDS, remaining))


_last_prune = [float("-inf")]


def maybe_prune(db: Session, now: datetime) -> None:
    """Delete commands older than PRUNE_AFTER, at most once an hour per API process."""
    if monotonic() - _last_prune[0] < PRUNE_EVERY_SECONDS:
        return
    _last_prune[0] = monotonic()
    db.execute(delete(MechanismCommand).where(MechanismCommand.created_at < now - PRUNE_AFTER))
    db.commit()
