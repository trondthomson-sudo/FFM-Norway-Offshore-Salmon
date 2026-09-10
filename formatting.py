"""
formatting.py
---------------
Small shared helper so both the console report (run_report.py) and the
Streamlit dashboard (streamlit_app.py) format big numbers the same way -
space-separated thousands (e.g. "250 000"), sensible decimal places -
without duplicating the formatting logic in two places.
"""

from __future__ import annotations
import pandas as pd
from datetime import datetime

THOUSANDS_SEP = " "  # change to "," here if you'd rather have commas


def _sep(formatted: str) -> str:
    """Python's ',' grouping option is the only built-in one; swap the
    character afterwards to get a space (or any other) separator instead."""
    return formatted.replace(",", THOUSANDS_SEP)


def with_thousands(df: pd.DataFrame, int_cols=(), float_cols=(), float_decimals: int = 2) -> pd.DataFrame:
    """
    Returns a COPY of df with the given columns rendered as strings using
    thousand separators, e.g. 1234567 -> "1 234 567" and 1234.5 -> "1 234.50".
    Only touches columns that are actually present, so it's safe to pass a
    fixed list of "columns we usually want formatted" regardless of which
    ones a particular table happens to include.
    """
    df = df.copy()
    for c in int_cols:
        if c in df.columns:
            df[c] = df[c].apply(lambda x: _sep(f"{x:,.0f}") if pd.notnull(x) else x)
    for c in float_cols:
        if c in df.columns:
            df[c] = df[c].apply(lambda x: _sep(f"{x:,.{float_decimals}f}") if pd.notnull(x) else x)
    return df


def fmt_int(x) -> str:
    """Format a single number with thousand separators, no decimals."""
    return _sep(f"{x:,.0f}")


def fmt_float(x, decimals: int = 2) -> str:
    """Format a single number with thousand separators and fixed decimals."""
    return _sep(f"{x:,.{decimals}f}")


def month_label(year_month: str) -> str:
    """'2027-03' -> 'Mar 2027' (matches the reference dashboard's axis style)."""
    return datetime.strptime(year_month, "%Y-%m").strftime("%b %Y")


def with_month_labels(df: pd.DataFrame, col: str = "year_month") -> pd.DataFrame:
    """Returns a COPY of df with `col` (e.g. '2027-03') rendered as 'Mar 2027'."""
    df = df.copy()
    if col in df.columns:
        df[col] = df[col].apply(month_label)
    return df


def parse_number(s: str, default: float = 0.0) -> float:
    """
    Parse a user-typed number that may contain thousand separators (spaces,
    commas, or non-breaking spaces), e.g. '250 000' or '250,000' -> 250000.0.
    Falls back to `default` if the text can't be parsed at all (e.g. empty
    or mid-edit), so a text_input built on this never crashes the app.
    """
    cleaned = s.replace(" ", "").replace(",", "").replace("\u00A0", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return default


def annuitet_manedsbelop(hovedstol: float, arlig_rente_brok: float, antall_maneder: int) -> float:
    """
    Standard annuitetslån-formel (likt månedlig beløp gjennom hele
    nedbetalingstiden): PMT = P x i / (1 - (1+i)^-n), der P = hovedstol,
    i = MÅNEDLIG rente (årlig rente / 12), n = antall måneder.
    `arlig_rente_brok` er en BRØKVERDI (0,12 for 12 % - ikke 12).
    Brukes for "Oppankring"-underlinjen i Hexacage-leien (config_1tank.py,
    HEXACAGE_LEIE_DEFAULTS) - et 5-års/60-måneders annuitetslån kunden
    (oppdretter) betaler ned.
    """
    if antall_maneder <= 0 or hovedstol <= 0:
        return 0.0
    i = arlig_rente_brok / 12.0
    if i == 0:
        return hovedstol / antall_maneder
    return hovedstol * i / (1 - (1 + i) ** (-antall_maneder))


def annuitetsplan(hovedstol: float, arlig_rente_brok: float, antall_maneder: int) -> pd.DataFrame:
    """
    Full nedbetalingsplan for et annuitetslån - én rad per måned, med
    inngående saldo, renter, avdrag, terminbeløp (renter + avdrag, likt
    hver måned) og utgående saldo. Brukes for å vise "13.2 Oppankring"
    sin oppbygning i detalj (i tilfelle noen spør hvordan tallet er
    beregnet) - se annuitet_manedsbelop() for selve terminbeløp-formelen.

    Siste rad justeres slik at utgående saldo blir eksakt 0,00 (unngår at
    avrundingsstøy over mange måneder etterlater noen få øre/kroner igjen).
    """
    if antall_maneder <= 0 or hovedstol <= 0:
        return pd.DataFrame(columns=["maned", "inngaende_saldo", "renter", "avdrag", "terminbelop", "utgaende_saldo"])

    terminbelop = annuitet_manedsbelop(hovedstol, arlig_rente_brok, antall_maneder)
    i = arlig_rente_brok / 12.0
    saldo = hovedstol
    rows = []
    for mnd in range(1, antall_maneder + 1):
        inngaende = saldo
        renter = inngaende * i
        avdrag = terminbelop - renter
        if mnd == antall_maneder:
            avdrag = inngaende  # siste rad - nullstill saldo eksakt
            terminbelop_rad = avdrag + renter
        else:
            terminbelop_rad = terminbelop
        utgaende = inngaende - avdrag
        rows.append({
            "maned": mnd, "inngaende_saldo": inngaende, "renter": renter,
            "avdrag": avdrag, "terminbelop": terminbelop_rad, "utgaende_saldo": utgaende,
        })
        saldo = utgaende
    return pd.DataFrame(rows)


def build_mass_balance_equations(summary: dict) -> list[str]:
    """
    Builds the two mass-balance equation strings for the biomass chart,
    from a summary dict (per-cycle from production_plan.plan_single_module,
    or annual totals from production_plan.run_annual_illustration - both
    use the same key names: initial_biomass_t, gross_growth_t,
    lost_biomass_t, delivered_biomass_t, net_volume_growth_t).

    Equation 1: Volum smolt kjopt inn + Bruttovekst - Tapt biomasse = Levert biomasse
    Equation 2: Levert biomasse - Kjopt smolt = Netto tilvekst
    (Equation 2 is simply net_volume_growth_t restated in words - it's the
    same number, delivered minus initial, computed directly rather than via
    gross growth and lost biomass.)
    """
    i = summary["initial_biomass_t"]
    g = summary["gross_growth_t"]
    l = summary["lost_biomass_t"]
    d = summary["delivered_biomass_t"]
    n = summary["net_volume_growth_t"]
    eq1 = f"{fmt_float(i,0)} t smolt kjopt inn + {fmt_float(g,0)} t bruttovekst - {fmt_float(l,0)} t tapt biomasse = {fmt_float(d,0)} t levert biomasse"
    eq2 = f"{fmt_float(d,0)} t levert biomasse - {fmt_float(i,0)} t kjopt smolt = {fmt_float(n,0)} t netto tilvekst"
    return [eq1, eq2]