"""Unit conversion for backfilled recorder states.

Home Assistant records a sensor in the unit it is *displayed* in, which can be changed per
entity, not in the unit the device publishes. On this install the gateway's distance is
recorded in inches and its battery in volts, though ESPHome publishes millimetres and
millivolts. A backfilled row has to match what HA itself would have written, so every
number is converted into the entity's current unit_of_measurement first.
"""
from __future__ import annotations

_LENGTH_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
_VOLTAGE_MV = {"mV": 1.0, "V": 1000.0}
_RATE_MM_H = {"mm/h": 1.0, "in/h": 25.4}
_TABLES = (_LENGTH_MM, _VOLTAGE_MV, _RATE_MM_H)


class UnitMismatch(ValueError):
    """The record's unit cannot be converted to the entity's."""


def convert(value: float, from_unit: str | None, to_unit: str | None) -> float:
    if (from_unit or "") == (to_unit or ""):
        return value
    for table in _TABLES:
        if from_unit in table and to_unit in table:
            return value * table[from_unit] / table[to_unit]
    if from_unit == "°F" and to_unit == "°C":
        return (value - 32.0) * 5.0 / 9.0
    if from_unit == "°C" and to_unit == "°F":
        return value * 9.0 / 5.0 + 32.0
    raise UnitMismatch(f"cannot convert {from_unit!r} to {to_unit!r}")


def format_state(value: float) -> str:
    """HA stores str(float). Six decimals keeps float noise out without losing anything a
    ±5 mm radar or a 0.01 in rain gauge can resolve."""
    return repr(round(float(value), 6))
