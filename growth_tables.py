"""
growth_tables.py
-----------------
Loads the two Skretting-derived lookup tables and exposes fast, interpolated
lookup functions used by the daily growth simulation:

  * FCR(weight_kg)          -> instantaneous feed conversion ratio (kg feed / kg gain)
  * SGR(weight_kg, temp_c)  -> daily specific growth rate, in % body weight / day

Both source tables cover 0-12 kg body weight in 0.01 kg steps. The SGR table
additionally covers 0-20 C in 0.5 C steps.

INTERPRETATION NOTE (important, please sanity-check against your own use of
these Skretting tables):
  We treat the SGR table value as a daily percentage growth rate applied as
  simple daily compounding:   W(t+1) = W(t) * (1 + SGR(W(t), T(t)) / 100)
  This is the standard way these Skretting-style "growth potential" tables are
  used in Norwegian production planning. If your internal convention instead
  uses the exponential/ln definition of SGR, swap `step_growth()` below for
  the exponential variant (also provided, commented out).
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.interpolate import RegularGridInterpolator

DATA_DIR = Path(__file__).parent / "data"


class GrowthTables:
    def __init__(self, fcr_csv: str | Path = None, sgr_csv: str | Path = None):
        fcr_csv = Path(fcr_csv) if fcr_csv else DATA_DIR / "fcr_table.csv"
        sgr_csv = Path(sgr_csv) if sgr_csv else DATA_DIR / "sgr_table.csv"

        # ---- FCR: 1D table, weight (kg) -> FCR ----
        fcr_df = pd.read_csv(fcr_csv).sort_values("kg")
        self._fcr_kg = fcr_df["kg"].to_numpy()
        self._fcr_val = fcr_df["fcr"].to_numpy()
        self._fcr_min_kg = self._fcr_kg.min()
        self._fcr_max_kg = self._fcr_kg.max()

        # ---- SGR: 2D table, (weight kg, temp C) -> SGR %/day ----
        sgr_df = pd.read_csv(sgr_csv).sort_values("kg")
        self._sgr_kg = sgr_df["kg"].to_numpy()
        temp_cols = [c for c in sgr_df.columns if c != "kg"]
        self._sgr_temps = np.array([float(c) for c in temp_cols])
        self._sgr_grid = sgr_df[temp_cols].to_numpy()  # shape (n_kg, n_temp)
        self._sgr_min_kg, self._sgr_max_kg = self._sgr_kg.min(), self._sgr_kg.max()
        self._sgr_min_t, self._sgr_max_t = self._sgr_temps.min(), self._sgr_temps.max()

        self._sgr_interp = RegularGridInterpolator(
            (self._sgr_kg, self._sgr_temps),
            self._sgr_grid,
            method="linear",
            bounds_error=False,
            fill_value=None,  # we'll clamp manually instead (extrapolate flat)
        )

    def fcr(self, weight_kg: float) -> float:
        """Instantaneous FCR at a given body weight (clamped to table domain)."""
        w = min(max(weight_kg, self._fcr_min_kg), self._fcr_max_kg)
        return float(np.interp(w, self._fcr_kg, self._fcr_val))

    def sgr(self, weight_kg: float, temp_c: float) -> float:
        """Daily SGR (% body weight/day) at a given weight and temperature."""
        w = min(max(weight_kg, self._sgr_min_kg), self._sgr_max_kg)
        t = min(max(temp_c, self._sgr_min_t), self._sgr_max_t)
        return float(self._sgr_interp([[w, t]])[0])

    def step_growth(self, weight_kg: float, temp_c: float, rgi_pct: float = 100.0) -> float:
        """
        One day of growth (simple daily-compounding SGR convention).

        rgi_pct: Relative Growth Index, as a percent of the Skretting
        table's own tabulated growth rate. 100 = exactly as tabulated
        (the table's own reference performance). E.g. 90 = fish growing
        at 90% of the Skretting reference SGR (underperforming that
        site/batch); 115 = growing 15% faster than the table.
        """
        sgr_pct = self.sgr(weight_kg, temp_c) * (rgi_pct / 100.0)
        return weight_kg * (1.0 + sgr_pct / 100.0)

    # Exponential/ln convention alternative (uncomment to use instead):
    # def step_growth(self, weight_kg: float, temp_c: float) -> float:
    #     sgr_pct = self.sgr(weight_kg, temp_c)
    #     return weight_kg * np.exp(sgr_pct / 100.0)


if __name__ == "__main__":
    gt = GrowthTables()
    print("FCR @ 0.1kg:", gt.fcr(0.1), " FCR @ 1kg:", gt.fcr(1.0), " FCR @ 5kg:", gt.fcr(5.0))
    print("SGR @ 0.1kg/10C:", gt.sgr(0.1, 10), " SGR @ 1kg/10C:", gt.sgr(1.0, 10))
