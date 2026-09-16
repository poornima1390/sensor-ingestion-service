"""SQLAlchemy models — the persistence shape, kept separate from the wire shape."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Reading(Base):
    __tablename__ = "readings"

    # Integer surrogate key rather than a UUID: it is monotonic, which makes it a
    # natural tiebreaker for stable pagination now and a keyset cursor later.
    id: Mapped[int] = mapped_column(primary_key=True)

    device_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Stored as a plain string rather than a SQL ENUM: adding a sensor type to a
    # Postgres enum needs a migration, adding one to a varchar does not.
    sensor_type: Mapped[str] = mapped_column(String(16), nullable=False)

    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # When the reading was measured.
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # When we received it. Sensor clocks drift and devices buffer while offline,
    # so these are different facts; keeping both makes ingestion lag measurable.
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_readings_device_timestamp", "device_id", "timestamp"),
        Index("ix_readings_type_timestamp", "sensor_type", "timestamp"),
        # Dedup rule. Sensor retries are routine, so a repeat of the same
        # measurement must not become a second row and skew the averages.
        UniqueConstraint("device_id", "sensor_type", "timestamp", name="uq_readings_natural_key"),
    )
