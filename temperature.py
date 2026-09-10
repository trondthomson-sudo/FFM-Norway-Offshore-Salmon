"""
temperature.py
---------------
Sea temperature profiles used to drive the growth simulation. Every profile
function here takes a datetime.date and returns a temperature in Celsius,
so they can be swapped in/out interchangeably in the simulator.

Hexacage draws water from adjustable intake depth (down to 55 m), so the
relevant temperature is whatever depth the operator chooses to draw from -
NOT necessarily surface temperature. For a real production plan, use
`monthly_profile()` with your own site's measured/forecast temperature at
intake depth (see config.py: MONTHLY_TEMPERATURES_C).
"""

from __future__ import annotations
from datetime import date
import numpy as np


def default_norway_coastal_profile(d: date, mean_c: float = 9.0,
                                     amplitude_c: float = 5.0,
                                     peak_day: int = 225) -> float:
    """
    Simple sinusoidal seasonal temperature model.
    Defaults: mean 9 C, amplitude +/-5 C (range ~4-14 C), peak ~mid-August (day 225).
    PLACEHOLDER ONLY - prefer monthly_profile() with real site data.
    """
    doy = d.timetuple().tm_yday
    return mean_c + amplitude_c * np.cos(2 * np.pi * (doy - peak_day) / 365.0)


def monthly_profile(monthly_avg_c: list[float]):
    """
    Build a date -> temperature function from 12 monthly average
    temperatures (Jan..Dec), linearly interpolated between month midpoints
    so the curve moves smoothly rather than jumping at month boundaries.
    This is what config.py's MONTHLY_TEMPERATURES_C feeds into.
    """
    assert len(monthly_avg_c) == 12, "Provide exactly 12 monthly values (Jan..Dec)"
    month_mid_days = np.array([15, 45, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349])
    vals = np.array(monthly_avg_c, dtype=float)
    ext_days = np.concatenate([[month_mid_days[-1] - 365], month_mid_days, [month_mid_days[0] + 365]])
    ext_vals = np.concatenate([[vals[-1]], vals, [vals[0]]])

    def profile(d: date) -> float:
        doy = d.timetuple().tm_yday
        return float(np.interp(doy, ext_days, ext_vals))

    return profile


if __name__ == "__main__":
    prof = monthly_profile([5, 4.5, 4.5, 5.5, 7.5, 10.5, 13, 14, 12.5, 10, 7.5, 6])
    for m, d in [(1, 1), (3, 15), (6, 1), (8, 15), (12, 20)]:
        print(f"{m:02d}-{d:02d}:", round(prof(date(2027, m, d)), 2))
