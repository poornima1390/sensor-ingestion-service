"""Sensor taxonomy: what types exist, what values are plausible, what units mean.

Kept free of Pydantic and SQLAlchemy imports so both the wire schema and the
storage model can depend on it without a circular import.
"""

from dataclasses import dataclass
from enum import StrEnum


class SensorType(StrEnum):
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    PRESSURE = "pressure"
    BATTERY = "battery"


@dataclass(frozen=True)
class SensorRule:
    """Plausible range and canonical unit for one sensor type.

    Ranges bracket physically plausible environmental readings rather than the
    limits of the sensor hardware: temperature covers industrial enclosures,
    pressure covers sea level through high altitude, and the two percentage
    types are bounded by definition.
    """

    minimum: float
    maximum: float
    unit: str

    def contains(self, value: float) -> bool:
        return self.minimum <= value <= self.maximum


SENSOR_RULES: dict[SensorType, SensorRule] = {
    SensorType.TEMPERATURE: SensorRule(-50.0, 150.0, "celsius"),
    SensorType.HUMIDITY: SensorRule(0.0, 100.0, "percent"),
    SensorType.PRESSURE: SensorRule(300.0, 1100.0, "hpa"),
    SensorType.BATTERY: SensorRule(0.0, 100.0, "percent"),
}

# Device clocks drift and buffer readings while offline. Rejecting a reading that
# is a few hundred milliseconds ahead of the server is a bug you find in
# production, not in tests, so the future check gets a small tolerance.
FUTURE_TOLERANCE_SECONDS = 60

MAX_DEVICE_ID_LENGTH = 64
