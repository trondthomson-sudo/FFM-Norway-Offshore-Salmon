"""
simulator.py
-------------
Day-by-day simulation of a single fish cohort from a starting weight,
plus monthly aggregation of the results.

TWO MODES - exactly one must be given when simulating a cohort:
  - target_weight_kg: run until this weight is reached. Cycle LENGTH is
    the output (how many days it takes to get there).
  - duration_days: run for exactly this many days. Harvest/transfer WEIGHT
    is the output (whatever weight the fish reach in that time).

The growth trajectory (fish weight over time) does NOT depend on population
size or mortality rate - only on starting weight, stopping condition and
temperature. So we compute it once, then separately apply stocking count
and mortality on top.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
import pandas as pd

from growth_tables import GrowthTables
from temperature import default_norway_coastal_profile


@dataclass
class CohortResult:
    daily: pd.DataFrame          # day-by-day trajectory (with population/feed)
    monthly: pd.DataFrame        # calendar-month aggregation
    cycle_days: int              # days in the growth phase
    start_weight_kg: float
    target_weight_kg: float      # ACTUAL final weight reached (output either way)
    stocked_count: int
    surviving_count: float
    total_mortality_pct: float
    final_biomass_kg: float
    total_feed_kg: float
    overall_fcr: float           # total feed / total biomass gain
    total_gross_growth_kg: float  # sum av bruttovekst - INKLUDERER veksten til fisk som senere dør


def simulate_growth_curve(
    growth_tables: GrowthTables,
    start_weight_kg: float,
    target_weight_kg: float,
    start_date: date,
    temp_profile_fn=default_norway_coastal_profile,
    max_days: int = 900,
    rgi_pct: float = 100.0,
) -> pd.DataFrame:
    """Run until `target_weight_kg` is reached (or max_days as a safety cap)."""
    weight = start_weight_kg
    day = 0
    rows = []

    while weight < target_weight_kg and day < max_days:
        this_date = start_date + timedelta(days=day)
        temp = temp_profile_fn(this_date)
        table_sgr_pct = growth_tables.sgr(weight, temp)
        sgr_pct = table_sgr_pct * (rgi_pct / 100.0)
        fcr = growth_tables.fcr(weight)
        new_weight = growth_tables.step_growth(weight, temp, rgi_pct=rgi_pct)

        rows.append({
            "day": day, "date": this_date, "temp_c": temp,
            "weight_kg": new_weight, "gain_kg": new_weight - weight,
            "sgr_pct_day": sgr_pct, "table_sgr_pct_day": table_sgr_pct,
            "rgi_pct": rgi_pct, "fcr": fcr,
        })
        weight = new_weight
        day += 1

    return pd.DataFrame(rows)


def simulate_growth_curve_duration(
    growth_tables: GrowthTables,
    start_weight_kg: float,
    duration_days: int,
    start_date: date,
    temp_profile_fn=default_norway_coastal_profile,
    rgi_pct: float = 100.0,
) -> pd.DataFrame:
    """Run for exactly `duration_days` days. Final weight is whatever it lands on."""
    weight = start_weight_kg
    rows = []

    for day in range(duration_days):
        this_date = start_date + timedelta(days=day)
        temp = temp_profile_fn(this_date)
        table_sgr_pct = growth_tables.sgr(weight, temp)
        sgr_pct = table_sgr_pct * (rgi_pct / 100.0)
        fcr = growth_tables.fcr(weight)
        new_weight = growth_tables.step_growth(weight, temp, rgi_pct=rgi_pct)

        rows.append({
            "day": day, "date": this_date, "temp_c": temp,
            "weight_kg": new_weight, "gain_kg": new_weight - weight,
            "sgr_pct_day": sgr_pct, "table_sgr_pct_day": table_sgr_pct,
            "rgi_pct": rgi_pct, "fcr": fcr,
        })
        weight = new_weight

    return pd.DataFrame(rows)


def daily_mortality_rate_for_total(total_mortality_pct: float, cycle_days: int) -> float:
    """Convert a target TOTAL mortality over the whole cycle (%) into a
    constant daily rate. Prefer a monthly mortality profile for real runs."""
    survival_frac = 1 - total_mortality_pct / 100.0
    if survival_frac <= 0 or cycle_days <= 0:
        return 0.0
    return 1 - survival_frac ** (1.0 / cycle_days)


def apply_population(
    growth_df: pd.DataFrame,
    stocked_count: int,
    daily_mortality_rate,
    start_weight_kg: float,
) -> CohortResult:
    """
    Adds population count, biomass and feed columns on top of a
    population-independent growth trajectory, and produces the
    calendar-month aggregation.

    `daily_mortality_rate` can be either a constant float (same rate every
    day) or a callable(date) -> daily rate (see mortality.monthly_mortality_profile).
    """
    df = growth_df.copy()
    count = float(stocked_count)
    counts, biomass, feed_day, cum_feed, gross_growth_day = [], [], [], [], []
    running_feed = 0.0
    rate_fn = daily_mortality_rate if callable(daily_mortality_rate) else (lambda d: daily_mortality_rate)

    for _, row in df.iterrows():
        # Bruttovekst = populasjon FØR dagens dødelighet x vekst per fisk -
        # altså tilveksten ALLE fisk (også de som dør senere samme dag/uke)
        # faktisk la på seg, FØR dødelighet trekkes fra. Samme prinsipp som
        # fôrforbruket under (som alltid har vært beregnet slik - fôr gitt
        # til fisk som senere dør, "forsvinner" ikke fra fôrregnskapet).
        gross_growth_today = count * row["gain_kg"]
        feed_today = count * row["gain_kg"] * row["fcr"]
        running_feed += feed_today
        count = count * (1 - rate_fn(row["date"]))

        counts.append(count)
        biomass.append(count * row["weight_kg"])
        feed_day.append(feed_today)
        cum_feed.append(running_feed)
        gross_growth_day.append(gross_growth_today)

    df["count_alive"] = counts
    df["biomass_kg"] = biomass
    df["feed_kg_day"] = feed_day
    df["cum_feed_kg"] = cum_feed
    df["gross_growth_kg_day"] = gross_growth_day

    surviving = df["count_alive"].iloc[-1] if len(df) else stocked_count
    final_weight = df["weight_kg"].iloc[-1] if len(df) else start_weight_kg
    total_mortality_pct = 100 * (1 - surviving / stocked_count)
    final_biomass = surviving * final_weight
    total_gain_biomass = final_biomass - stocked_count * start_weight_kg
    total_feed_kg = df["cum_feed_kg"].iloc[-1] if len(df) else 0.0
    overall_fcr = total_feed_kg / total_gain_biomass if total_gain_biomass > 0 else float("nan")
    total_gross_growth_kg = df["gross_growth_kg_day"].sum() if len(df) else 0.0

    # ---- Calendar-month aggregation ----
    df["year_month"] = df["date"].apply(lambda d: d.strftime("%Y-%m"))
    df["start_of_day_weight_kg"] = df["weight_kg"] - df["gain_kg"]
    monthly = (
        df.groupby("year_month", sort=False)
        .agg(
            days_in_cycle=("day", "count"),
            avg_temp_c=("temp_c", "mean"),
            start_weight_kg=("start_of_day_weight_kg", "first"),
            end_weight_kg=("weight_kg", "last"),
            end_count_alive=("count_alive", "last"),
            end_biomass_kg=("biomass_kg", "last"),
            feed_this_month_kg=("feed_kg_day", "sum"),
        )
        .reset_index()
    )
    monthly["end_biomass_t"] = monthly["end_biomass_kg"] / 1000
    monthly["feed_this_month_t"] = monthly["feed_this_month_kg"] / 1000
    monthly["cum_feed_t"] = monthly["feed_this_month_t"].cumsum()

    return CohortResult(
        daily=df,
        monthly=monthly,
        cycle_days=len(df),
        start_weight_kg=start_weight_kg,
        target_weight_kg=final_weight,
        stocked_count=stocked_count,
        surviving_count=surviving,
        total_mortality_pct=total_mortality_pct,
        final_biomass_kg=final_biomass,
        total_feed_kg=total_feed_kg,
        overall_fcr=overall_fcr,
        total_gross_growth_kg=total_gross_growth_kg,
    )


def simulate_cohort(
    growth_tables: GrowthTables,
    start_weight_kg: float,
    stocked_count: int,
    target_weight_kg: float = None,
    duration_days: int = None,
    mortality_profile_fn=None,
    total_cycle_mortality_pct: float = None,
    start_date: date = None,
    temp_profile_fn=default_norway_coastal_profile,
    max_days: int = 900,
    rgi_pct: float = 100.0,
) -> CohortResult:
    """
    Provide EXACTLY ONE of target_weight_kg / duration_days:
      - target_weight_kg: cycle length is computed for you.
      - duration_days: harvest/transfer weight is computed for you.

    Mortality: pass EITHER mortality_profile_fn (callable(date) -> daily
    rate, e.g. from mortality.monthly_mortality_profile - preferred) OR
    total_cycle_mortality_pct (spread evenly across the cycle).
    """
    assert (target_weight_kg is not None) ^ (duration_days is not None), \
        "Provide exactly one of target_weight_kg or duration_days"

    start_date = start_date or date(date.today().year, 3, 1)

    if duration_days is not None:
        growth_df = simulate_growth_curve_duration(
            growth_tables, start_weight_kg, duration_days, start_date,
            temp_profile_fn, rgi_pct,
        )
    else:
        growth_df = simulate_growth_curve(
            growth_tables, start_weight_kg, target_weight_kg, start_date,
            temp_profile_fn, max_days, rgi_pct,
        )

    if mortality_profile_fn is not None:
        rate = mortality_profile_fn
    elif total_cycle_mortality_pct is not None:
        cycle_days = len(growth_df)
        rate = daily_mortality_rate_for_total(total_cycle_mortality_pct, cycle_days)
    else:
        rate = 0.0

    return apply_population(growth_df, stocked_count, rate, start_weight_kg)


if __name__ == "__main__":
    from mortality import monthly_mortality_profile
    gt = GrowthTables()
    mort_fn = monthly_mortality_profile([0.5] * 12)

    print("--- Mode 1: target_weight_kg (cycle length is the output) ---")
    res = simulate_cohort(gt, start_weight_kg=0.1, stocked_count=250_000,
                           target_weight_kg=1.0, mortality_profile_fn=mort_fn,
                           start_date=date(2027, 3, 1))
    print(f"Cycle length: {res.cycle_days} days, final weight: {res.target_weight_kg:.2f} kg")

    print("\n--- Mode 2: duration_days (harvest weight is the output) ---")
    res2 = simulate_cohort(gt, start_weight_kg=0.35, stocked_count=250_000,
                            duration_days=round(11.5 * 30.44), mortality_profile_fn=mort_fn,
                            start_date=date(2027, 3, 1))
    print(f"Cycle length: {res2.cycle_days} days, final weight: {res2.target_weight_kg:.2f} kg")
