"""Camera station mechanism over the cloud relay (docs/cloud-motor-control.md).

With ADGVS_MECHANISM_MODE=relay the API never connects to the Pi. Operator commands wait in
mechanism_commands until the station's relay process long-polls them, and the relay reports
the bridge's state into mechanism_station_states. Neither table holds student data.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, JSONType
from app.models.enums import MechanismAction


class MechanismCommand(Base):
    """One command for one station. Delivered at most once; stop/disconnect never expire."""

    __tablename__ = "mechanism_commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("scan_stations.id"), index=True)
    action: Mapped[MechanismAction] = mapped_column(
        Enum(MechanismAction, native_enum=False, length=20))
    # Fixed by the backend (session id, tray angle, fixed VALID/INVALID angles); never client
    # calibration values.
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # None: never
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # {"ok": bool, "error": str | None}: what the relay reported, or why it was dropped.
    result: Mapped[dict | None] = mapped_column(JSONType)


class MechanismStationState(Base):
    """The relay's latest report for a station, plus who controls it (one row per station)."""

    __tablename__ = "mechanism_station_states"

    station_id: Mapped[int] = mapped_column(
        ForeignKey("scan_stations.id"), primary_key=True, autoincrement=False)
    # The bridge's /integration/status as last reported (motor state, workflow stage).
    state: Mapped[dict | None] = mapped_column(JSONType)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The Pi's local opt-in (relay --allow-remote); without it only stop/disconnect run.
    remote_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    # One controller: the operator whose commands and heartbeats count, and when they were
    # last heard from. The relay keeps the bridge's dead-man alive only while that is fresh.
    controller_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    controller_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Someone is watching the camera preview: the relay then pushes frames (about 1 fps).
    viewer_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
