import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PilotRole(str, enum.Enum):
    PILOT = "pilot"
    ADMIN = "admin"


class PilotStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class FlightSource(str, enum.Enum):
    AUTO = "auto"       # detected by the OGN tracker
    MANUAL = "manual"   # entered by hand (admin/pilot correction, no OGN beacon match)


class Pilot(Base):
    __tablename__ = "pilots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[PilotRole] = mapped_column(Enum(PilotRole), default=PilotRole.PILOT, nullable=False)
    status: Mapped[PilotStatus] = mapped_column(Enum(PilotStatus), default=PilotStatus.PENDING, nullable=False)
    vereinsflieger_member_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(back_populates="pilot")

    @property
    def is_approved(self) -> bool:
        return self.status == PilotStatus.APPROVED

    @property
    def is_admin(self) -> bool:
        return self.role == PilotRole.ADMIN


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    pilot: Mapped["Pilot"] = relationship(back_populates="reset_tokens")


class Airfield(Base):
    __tablename__ = "airfields"

    icao: Mapped[str] = mapped_column(String(4), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class Glider(Base):
    __tablename__ = "gliders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    registration: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ogn_device_id: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    # Opaque, unguessable token embedded in the glider's QR code: /claim/{claim_token}
    claim_token: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4())
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class GliderClaim(Base):
    """A pilot scanning a glider's QR code before takeoff.

    Consumed when the tracker matches a takeoff on this glider to this claim; the
    resulting Flight then carries the pilot automatically.
    """

    __tablename__ = "glider_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    glider_id: Mapped[int] = mapped_column(ForeignKey("gliders.id"), nullable=False)
    pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id"), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    flight_id: Mapped[int | None] = mapped_column(ForeignKey("flights.id"), nullable=True)

    glider: Mapped["Glider"] = relationship()
    pilot: Mapped["Pilot"] = relationship()


class Flight(Base):
    __tablename__ = "flights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # UUID assigned by the tracker at takeoff detection time; stable join key across
    # the CSV/OGN pipeline and this table.
    record_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)

    glider_id: Mapped[int | None] = mapped_column(ForeignKey("gliders.id"), nullable=True)
    pilot_id: Mapped[int | None] = mapped_column(ForeignKey("pilots.id"), nullable=True)

    takeoff_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    landing_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    takeoff_airfield_icao: Mapped[str | None] = mapped_column(ForeignKey("airfields.icao"), nullable=True)
    landing_airfield_icao: Mapped[str | None] = mapped_column(ForeignKey("airfields.icao"), nullable=True)

    # Raw coordinates of the landing point. Populated whenever no known airfield was
    # within the detection radius, i.e. a candidate outlanding, so it isn't just
    # silently dropped.
    landing_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    landing_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    duration_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[FlightSource] = mapped_column(Enum(FlightSource), default=FlightSource.AUTO, nullable=False)

    verified_by_pilot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    vereinsflieger_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vereinsflieger_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    glider: Mapped["Glider | None"] = relationship()
    pilot: Mapped["Pilot | None"] = relationship()

    @property
    def is_possible_outlanding(self) -> bool:
        return self.landing_time is not None and self.landing_airfield_icao is None


class FlightAuditEntry(Base):
    """Records every correction made to a flight, by whom, for accountability."""

    __tablename__ = "flight_audit_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flight_id: Mapped[int] = mapped_column(ForeignKey("flights.id"), nullable=False)
    changed_by_pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
