import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    LargeBinary,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Column,
    Text,
    UniqueConstraint,
    and_,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Flugart, with Vereinsflieger's codes (incl. the SG Bern specific ones).
FLIGHT_TYPES = {  # in Vereinsflieger's order
    "C": "Checkflug", "F": "F-Schlepp", "P": "Passagierflug", "L": "Leistungseinweisung", "V": "Werkverkehr",
    "S": "Schulflug", "N": "Privatflug", "B": "Befähigungsüberprüfung", "Ü": "Auffrischungsschulung",
    "E": "Einweisung", "ES": "Einweisung Startart", "EF": "Einfliegen", "PF": "Prüfungsflug",
    "SF": "Schnupperflug", "FS": "F-Schlepp-Schulung",
}

# Abrechnungsart: who pays, as in Vereinsflieger.
BILLING_TYPES = {
    "none": "Keine", "pilot": "Pilot", "companion": "Begleiter", "pilot_companion": "Pilot + Begleiter",
    "guest": "Gastflug", "guest_pilot_pays": "Gastflug (Pilot zahlt)", "other_member": "Anderes Mitglied",
}


class PilotRole(str, enum.Enum):
    PILOT = "pilot"
    ADMIN = "admin"
    # Flugdienstleiter: the account of whoever runs the day's flying (club
    # laptop at the launch point). Sees who is checked in, flying and gone
    # home; may edit and add all flights of open days and check pilots out.
    # Never flies itself (no check-ins), no user/aircraft management.
    FDL = "fdl"


class PilotStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class FlightSource(str, enum.Enum):
    AUTO = "auto"       # detected by the OGN tracker
    MANUAL = "manual"   # entered by hand (admin/pilot correction, no OGN beacon match)


class AircraftKind(str, enum.Enum):
    GLIDER = "glider"            # Segelflugzeug
    MOTORGLIDER = "motorglider"  # Motorsegler (self-launching / TMG)
    TOWPLANE = "towplane"        # Schleppflugzeug

    @property
    def label(self) -> str:
        return {"glider": "Segelflugzeug", "motorglider": "Motorsegler", "towplane": "Schleppflugzeug"}[self.value]

    @property
    def can_tow(self) -> bool:
        """Tow planes, and motor gliders (they can tow gliders too)."""
        return self is not AircraftKind.GLIDER

    @property
    def flies_all_day(self) -> bool:
        """Usually one pilot for many flights (tow pilot, motor glider trip), so
        checking in defaults to 'for the whole day'."""
        return self is not AircraftKind.GLIDER


class LaunchMethod(str, enum.Enum):
    """Startart, with the letters Vereinsflieger uses."""

    AEROTOW = "F"  # F-Schlepp
    WINCH = "W"    # Windenstart
    SELF = "E"     # Eigenstart (motor glider, tow plane)

    @property
    def label(self) -> str:
        return {"F": "F-Schlepp", "W": "Winde", "E": "Eigenstart"}[self.value]


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

    @property
    def edits_all_flights(self) -> bool:
        return self.role in (PilotRole.ADMIN, PilotRole.FDL)

    @property
    def is_fdl(self) -> bool:
        return self.role is PilotRole.FDL


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

    # ICAO code, or the OurAirports ident (e.g. "CH-0012") for fields without one.
    icao: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


# Members who own a private aircraft (one or several, e.g. a syndicate).
aircraft_owners = Table(
    "aircraft_owners", Base.metadata,
    Column("glider_id", ForeignKey("gliders.id", ondelete="CASCADE"), primary_key=True),
    Column("pilot_id", ForeignKey("pilots.id", ondelete="CASCADE"), primary_key=True),
)


class Glider(Base):
    """An aircraft the tracker follows: the club's own, or a member's private
    one (it has owners). Private aircraft are tracked and logged like the
    club's and listed for check-in (marked "privat"), and a private motor
    glider can tow like the club's - but a private aircraft is never shown as
    "frei" (not the club's to hand out)."""

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
    kind: Mapped[AircraftKind] = mapped_column(
        Enum(AircraftKind), default=AircraftKind.GLIDER, server_default=AircraftKind.GLIDER.name, nullable=False
    )
    owners: Mapped[list["Pilot"]] = relationship(secondary=aircraft_owners, order_by="Pilot.full_name", lazy="selectin")

    @property
    def is_private(self) -> bool:
        return bool(self.owners)

    def owned_by(self, pilot: "Pilot") -> bool:
        return any(owner.id == pilot.id for owner in self.owners)


class GliderClaim(Base):
    """A pilot checking in on an aircraft (QR code or list) before takeoff.

    The tracker gives each takeoff the pilot of the most recent active claim.
    A normal claim is used up by that takeoff (`consumed_at`, `flight_id`); a
    whole-day claim (tow pilot, motor glider trip) stays active for every
    takeoff until it expires at local midnight or is cancelled (released by
    the pilot, or at checkout).
    """

    __tablename__ = "glider_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    glider_id: Mapped[int] = mapped_column(ForeignKey("gliders.id"), nullable=False)
    pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id"), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    flight_id: Mapped[int | None] = mapped_column(ForeignKey("flights.id"), nullable=True)
    whole_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who ended it: the pilot themselves, someone taking the aircraft over,
    # the FDL, an admin. Empty for older rows and automatic ends.
    cancelled_by_id: Mapped[int | None] = mapped_column(ForeignKey("pilots.id"), nullable=True)
    # Who made the check-in, if not the pilot: the FDL on the club PC.
    claimed_by_id: Mapped[int | None] = mapped_column(ForeignKey("pilots.id"), nullable=True)

    glider: Mapped["Glider"] = relationship()
    pilot: Mapped["Pilot"] = relationship(foreign_keys=[pilot_id])
    cancelled_by: Mapped["Pilot | None"] = relationship(foreign_keys=[cancelled_by_id])

    @classmethod
    def active_at(cls, when: datetime):
        """SQL condition: claim can still be matched to a takeoff at `when`."""
        return and_(cls.consumed_at.is_(None), cls.cancelled_at.is_(None),
                    cls.claimed_at <= when, cls.expires_at > when)


class Checkout(Base):
    """A pilot has checked out for the day: flights confirmed, going home.

    Checking in again later that day makes them present again (a check-in
    after `checked_out_at`); checking out again moves `checked_out_at`.
    """

    __tablename__ = "checkouts"
    __table_args__ = (UniqueConstraint("pilot_id", "day", name="checkouts_pilot_day_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)  # local flying day
    checked_out_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    by_pilot_id: Mapped[int] = mapped_column(ForeignKey("pilots.id"), nullable=False)  # the pilot, or the FDL

    pilot: Mapped["Pilot"] = relationship(foreign_keys=[pilot_id])


class Flight(Base):
    __tablename__ = "flights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # UUID assigned by the tracker at takeoff detection time; stable join key across
    # the CSV/OGN pipeline and this table.
    record_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)

    glider_id: Mapped[int | None] = mapped_column(ForeignKey("gliders.id"), nullable=True)
    pilot_id: Mapped[int | None] = mapped_column(ForeignKey("pilots.id"), nullable=True, index=True)
    # Pilot without an account (guest, trial flight): name as free text.
    pilot_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Begleiter / second seat (instructor, student, passenger): member or free text.
    companion_id: Mapped[int | None] = mapped_column(ForeignKey("pilots.id"), nullable=True)
    companion_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    landings: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    flight_type: Mapped[str] = mapped_column(String(4), default="N", server_default="N", nullable=False)
    billing: Mapped[str] = mapped_column(String(24), default="pilot", server_default="pilot", nullable=False)
    # With billing "other_member": who pays.
    billing_member_id: Mapped[int | None] = mapped_column(ForeignKey("pilots.id"), nullable=True)
    # Tow of an aerotow launch as entered/confirmed by a person. The tracker's
    # link to the tow plane's own flight (tow_flight_id) fills in what's missing.
    tow_glider_id: Mapped[int | None] = mapped_column(ForeignKey("gliders.id"), nullable=True)
    tow_pilot_id: Mapped[int | None] = mapped_column(ForeignKey("pilots.id"), nullable=True)

    takeoff_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    landing_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    takeoff_airfield_icao: Mapped[str | None] = mapped_column(ForeignKey("airfields.icao"), nullable=True)
    landing_airfield_icao: Mapped[str | None] = mapped_column(ForeignKey("airfields.icao"), nullable=True)

    # Raw coordinates of the landing point as detected. For a candidate outlanding
    # (no known airfield within range) this is the only location we have.
    landing_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    landing_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # True when the tracker did not see the takeoff/landing itself (e.g. out of
    # receiver coverage, FLARM switched off) and the time is its best guess.
    takeoff_estimated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    landing_estimated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    duration_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[FlightSource] = mapped_column(Enum(FlightSource), default=FlightSource.AUTO, nullable=False)

    verified_by_pilot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    vereinsflieger_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vereinsflieger_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    launch_method: Mapped[LaunchMethod | None] = mapped_column(Enum(LaunchMethod), nullable=True)
    # For a towed glider flight: the tow plane's flight (tow pilot, tow time).
    tow_flight_id: Mapped[int | None] = mapped_column(ForeignKey("flights.id"), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    # Soft delete (false detection, duplicate): hidden everywhere, history kept.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    glider: Mapped["Glider | None"] = relationship(foreign_keys=[glider_id])
    pilot: Mapped["Pilot | None"] = relationship(foreign_keys=[pilot_id])
    companion: Mapped["Pilot | None"] = relationship(foreign_keys=[companion_id])
    billing_member: Mapped["Pilot | None"] = relationship(foreign_keys=[billing_member_id])
    tow_glider: Mapped["Glider | None"] = relationship(foreign_keys=[tow_glider_id])
    tow_pilot: Mapped["Pilot | None"] = relationship(foreign_keys=[tow_pilot_id])
    tow_flight: Mapped["Flight | None"] = relationship(remote_side="Flight.id", foreign_keys=[tow_flight_id])

    @property
    def is_possible_outlanding(self) -> bool:
        return self.landing_time is not None and self.landing_airfield_icao is None

    @property
    def pilot_display(self) -> str | None:
        return self.pilot.full_name if self.pilot else self.pilot_name

    @property
    def companion_display(self) -> str | None:
        return self.companion.full_name if self.companion else self.companion_name

    @property
    def tow_aircraft(self) -> "Glider | None":
        return self.tow_glider or (self.tow_flight.glider if self.tow_flight else None)

    @property
    def tow_pilot_display(self) -> str | None:
        if self.tow_pilot:
            return self.tow_pilot.full_name
        return self.tow_flight.pilot_display if self.tow_flight else None

    @property
    def flight_type_label(self) -> str:
        return FLIGHT_TYPES.get(self.flight_type, self.flight_type)

    @property
    def billing_label(self) -> str:
        return BILLING_TYPES.get(self.billing, self.billing)

    @property
    def needs_attention(self) -> list[str]:
        """What someone should check before this flight is correct (German)."""
        issues = []
        if self.landing_time is None:
            issues.append("keine Landung")
        if self.pilot_display is None:
            issues.append("Pilot fehlt")
        if self.takeoff_estimated or self.landing_estimated:
            issues.append("Zeit geschätzt")
        if self.is_possible_outlanding:
            issues.append("Landeort fehlt")
        return issues


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

    changed_by: Mapped["Pilot"] = relationship()


class TrackPoint(Base):
    """One position of a flight while it's in the air (live map). At the
    landing they are packed into one FlightTrack row and deleted here."""

    __tablename__ = "track_points"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    flight_id: Mapped[int] = mapped_column(ForeignKey("flights.id", ondelete="CASCADE"), nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    altitude_m: Mapped[float] = mapped_column(Float, nullable=False)  # GPS, above sea level
    ground_m: Mapped[float | None] = mapped_column(Float, nullable=True)  # terrain below (SRTM)
    speed_kmh: Mapped[float] = mapped_column(Float, nullable=False)
    climb_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class FlightTrack(Base):
    """The whole track of a landed flight, packed (shared/tracks.py): about
    30 kB for a three-hour flight instead of thousands of rows."""

    __tablename__ = "flight_tracks"

    flight_id: Mapped[int] = mapped_column(ForeignKey("flights.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    point_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Where it was last seen - for a flight whose landing we didn't see, where contact was lost.
    last_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    last_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    last_altitude_m: Mapped[float] = mapped_column(Float, nullable=False)
