"""Tridonic Lifetime Indicator values, reported in DALI memory bank 4.

Memory bank 4 is Tridonic's manufacturer-specific bank, so these definitions
only describe gear built by Tridonic. Defined here are the Lifetime Indicator
values at 0x42-0x67, as specified in "SPEC Lifetime Indicator LED Drivers"
(FB-5632, Lifetime Indicator v2.0).

A driver derives the values from the modelled ageing of the aluminium
electrolytic capacitors it is built around, on the assumption that they are
its weakest component, and recalculates them every five minutes.
"""

from decimal import Decimal

from dali.memory.location import (
    FixedScaleNumericValue,
    MemoryBank,
    MemoryLocation,
    MemoryType,
    MemoryValue,
)

# 0x67 is the last location of the Lifetime Indicator block, which ends the
# bank. The lock byte at 0x02 follows DALI version 2 but has no effect.
BANK_4 = MemoryBank(4, 0x67, has_lock=True)

# Unit the driver reports both the current and the averaged ageing rates in.
UNIT_PERCENT_PER_YEAR = "%/year"


class DriverHealth(FixedScaleNumericValue):  # type: ignore[misc]
    """Remaining health of the driver, as a percentage.

    Starts at 100% and falls to 0% as the capacitors reach the end of their
    operational life, at which point the driver may no longer perform within
    its specified ranges.
    """

    bank = BANK_4
    unit = "%"
    scaling_factor = Decimal("0.5")
    max_value = 200
    locations = MemoryLocation(address=0x42, default=0xC8, type_=MemoryType.ROM)


class CurrentAgingRate(FixedScaleNumericValue):  # type: ignore[misc]
    """How fast the capacitors are ageing right now, in percent per year.

    The reciprocal of the capacitor lifetime at the present temperature, so a
    measure of how hard the driver is being stressed rather than of how worn
    it already is.
    """

    bank = BANK_4
    unit = UNIT_PERCENT_PER_YEAR
    scaling_factor = Decimal("0.2")
    max_value = 250
    locations = MemoryLocation(address=0x43, default=0x00, type_=MemoryType.ROM)


class AgingRateLastMonth(FixedScaleNumericValue):  # type: ignore[misc]
    """Ageing rate averaged over the last month (744 h), in percent per year.

    A driver reports nothing for its first month of operation, before the
    exponential moving average behind this value has a meaningful history.
    """

    bank = BANK_4
    unit = UNIT_PERCENT_PER_YEAR
    scaling_factor = Decimal("0.1")
    max_value = 250
    locations = MemoryLocation(address=0x44, default=0x00, type_=MemoryType.ROM)


class AgingRateLastYear(FixedScaleNumericValue):  # type: ignore[misc]
    """Ageing rate averaged over the last year (8760 h), in percent per year.

    Reported from the first month of operation onwards, as with the monthly
    average, rather than only once a year of history exists.
    """

    bank = BANK_4
    unit = UNIT_PERCENT_PER_YEAR
    scaling_factor = Decimal("0.1")
    max_value = 250
    locations = MemoryLocation(address=0x45, default=0x00, type_=MemoryType.ROM)


# Control-gear temperature ranges the driver logs its operating time in, as
# inclusive bounds in degrees Celsius. An open bound means the outermost range
# is unbounded in that direction. The resolution tightens as the temperature
# rises, because that is where ageing accelerates.
TEMPERATURE_RANGES: tuple[tuple[int | None, int | None], ...] = (
    (None, -20),
    (-19, 0),
    (1, 20),
    (21, 30),
    (31, 40),
    (41, 50),
    (51, 60),
    (61, 65),
    (66, 70),
    (71, 75),
    (76, 80),
    (81, 85),
    (86, 90),
    (91, 95),
    (96, 100),
    (101, 105),
    (106, None),
)

OPERATING_TIME_FIRST_ADDRESS = 0x46


def _operating_time_value(index: int) -> type[MemoryValue]:
    """Declare the operating-time counter for one temperature range."""
    low_byte = OPERATING_TIME_FIRST_ADDRESS + index * 2
    return type(
        f"OperatingTimeTemperatureRange{index + 1}",
        (FixedScaleNumericValue,),
        {
            "__doc__": (
                f"Hours the driver has operated within temperature range {index + 1}."
            ),
            "bank": BANK_4,
            "unit": "h",
            "scaling_factor": 2,
            # The counter is stored least significant byte first, so the two
            # locations are declared in descending address order for the
            # big-endian base class.
            "locations": (
                MemoryLocation(
                    address=low_byte + 1, default=0x00, type_=MemoryType.ROM
                ),
                MemoryLocation(address=low_byte, default=0x00, type_=MemoryType.ROM),
            ),
        },
    )


OPERATING_TIME_VALUES: tuple[type[MemoryValue], ...] = tuple(
    _operating_time_value(index) for index in range(len(TEMPERATURE_RANGES))
)
