"""Scan station registration (Section 18: each station has its own API key)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_or_404, require_roles
from app.db import get_db
from app.models import ScanStation, User
from app.models.enums import Role
from app.schemas import ReasonIn
from app.security import hash_token, new_token
from app.services import audit

router = APIRouter(prefix="/stations", tags=["stations"])
admin = require_roles(Role.ADMIN)


class StationIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class StationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    is_active: bool
    created_at: datetime


class StationCreatedOut(StationOut):
    api_key: str  # shown once; only its hash is stored


@router.get("", response_model=list[StationOut])
def list_stations(_: User = Depends(admin), db: Session = Depends(get_db)):
    return db.scalars(select(ScanStation).order_by(ScanStation.id)).all()


@router.post("", response_model=StationCreatedOut, status_code=status.HTTP_201_CREATED)
def register_station(body: StationIn, request: Request, actor: User = Depends(admin),
                     db: Session = Depends(get_db)):
    key = new_token()
    station = ScanStation(name=body.name, api_key_hash=hash_token(key), registered_by=actor.id)
    db.add(station)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Station name already exists") from None
    audit.record(db, "station_registered", user_id=actor.id, entity_type="scan_station",
                 entity_id=station.id, new_value={"name": body.name},
                 ip_address=client_ip(request))
    db.commit()
    db.refresh(station)
    return StationCreatedOut(id=station.id, name=station.name, is_active=station.is_active,
                             created_at=station.created_at, api_key=key)


@router.post("/{station_id}/deactivate", response_model=StationOut)
def deactivate_station(station_id: int, body: ReasonIn, request: Request,
                       actor: User = Depends(admin), db: Session = Depends(get_db)):
    station = get_or_404(db, ScanStation, station_id)
    station.is_active = False
    audit.record(db, "station_deactivated", user_id=actor.id, entity_type="scan_station",
                 entity_id=station.id, new_value={"reason": body.reason},
                 ip_address=client_ip(request))
    db.commit()
    return station
