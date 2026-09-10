"""
scheduler_1tank.py - postsmolt, MANUELT TIDSSTYRT, ÉN TANK
------------------------------------------------------------------------------
Forenklet variant av scheduler_manuell.py: samme rotasjons-idé (du definerer
N ulike "oppskrifter" som gjentas automatisk - typisk 4, én per kvartal),
men INGEN split til vekstkar. Én tank tar imot smolt, vokser i
GROWTH_WEEKS uker, og hele kohorten leveres/selges direkte som postsmolt
derfra. Tanken vaskes deretter (CLEANING_WEEKS) før neste oppskrift i
rotasjonen settes inn.

Dette er ment som byggekloss #1 i en større modell (etter hvert 36 kohorter,
flere tanker, batch-nivå PnL/kontantstrøm/balanse) - resource_ledger.py
bygger et ressursregnskap (materialer + timer) direkte oppå det denne
scheduleren produserer, radvis per kohort x uke, slik at datamodellen ikke
må bygges om når antall tanker/kohorter skalerer opp.

Vekstmotoren (Skretting SGR/FCR + temperaturprofil + RGI) er identisk med
de andre modellene i familien - kun tidspunkt/smoltantall er manuelt.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
import pandas as pd

from growth_tables import GrowthTables
from simulator import simulate_cohort, CohortResult
from temperature import monthly_profile


def monday_of_week(week_index: int, start_iso_year: int, start_iso_week: int) -> date:
    monday0 = date.fromisocalendar(start_iso_year, start_iso_week, 1)
    return monday0 + timedelta(weeks=week_index)


def week_label(week_index: int, start_iso_year: int, start_iso_week: int) -> tuple[str, date]:
    d = monday_of_week(week_index, start_iso_year, start_iso_week)
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-U{iso_week:02d}", d


def daily_mortality_rate(annual_pct: float) -> float:
    survival_frac = 1 - annual_pct / 100.0
    if survival_frac <= 0:
        return 1.0
    return 1 - survival_frac ** (1.0 / 365.0)


@dataclass
class Cohort:
    id: str
    tank: str
    start_week: int
    n_weeks: int
    result: CohortResult
    weekly_biomass_kg: list          # UBESKATTET vekstkurve (ingen harvest-reduksjon) - brukes til
                                       # kg_wfe_netto/bruttovekst-beregninger (ren biologisk vekst)
    weekly_weight_kg: list
    weekly_count_alive: list         # UBESKATTET (naturlig dødelighet, IKKE harvest-reduksjon)
    weekly_feed_kg: list
    weekly_gross_growth_kg: list
    weekly_standing_biomass_kg: list = None  # STÅENDE biomasse i tanken (harvest-justert i
                                               # salgsvinduet) - brukes KUN til visning (kalender/
                                               # graf/tetthet), IKKE til vekstberegninger.
    weekly_standing_count: list = None       # tilsvarende for antall fisk

    def __post_init__(self):
        if self.weekly_standing_biomass_kg is None:
            self.weekly_standing_biomass_kg = list(self.weekly_biomass_kg)
        if self.weekly_standing_count is None:
            self.weekly_standing_count = list(self.weekly_count_alive)


def _split_i_batcher(c: "Cohort", info: dict, salgsvindu_uker: int) -> list[dict]:
    """Splitter kohortens SISTE `salgsvindu_uker` vekstuker opp i like mange
    batcher, solgt med AVTAKENDE ANDEL av gjenværende bestand: 1/N i første
    salgsuke, 1/(N-1) av det som er igjen i neste, ..., HELE resten i siste
    uke. Dette gir JEVNSTORE batcher (likt antall fisk per batch), siden
    1/N + (N-1)/N x 1/(N-1) + ... alltid summerer til N like store deler.

    Oppdaterer c.weekly_standing_biomass_kg/weekly_standing_count for
    salgsvinduets uker (STÅENDE biomasse/antall i tanken, harvest-justert -
    brukt KUN til visning: kalender, graf, tetthet/MAB). De OPPRINNELIGE
    c.weekly_biomass_kg/weekly_count_alive røres IKKE - de representerer
    ren biologisk vekst (uavhengig av salg) og brukes fortsatt til
    kg_wfe_netto/bruttovekst-beregningene i resource_ledger.py. Uten dette
    skillet ville "netto tilvekst" feilaktig tolket selve SALGET som
    negativ vekst (biomassen i tanken synker jo når fisk selges, men det
    er ikke det samme som at fisken sluttet å vokse).

    Naturlig dødelighet i salgsvinduet hentes fra den ALLEREDE simulerte
    (ubeskattede) uke-for-uke-kurven (weekly_count_alive) - vekt per fisk
    (weekly_weight_kg) er upåvirket av bestandsstørrelse og gjenbrukes
    uendret.

    salgsvindu_uker <= 1 gir uendret oppførsel: én batch, hele biomassen
    levert i siste vekstuke (som før)."""
    n = min(max(1, salgsvindu_uker), c.n_weeks)
    if n <= 1:
        return [{
            "batch_id": f"{c.id}-B1", "delivery_week": c.start_week + c.n_weeks - 1,
            "delivered_count": info["survivors_at_delivery"],
            "delivered_biomass_kg": info["delivered_biomass_kg"],
            "avg_weight_kg": info["delivery_weight_kg"],
        }]

    start_idx = c.n_weeks - n
    # Naturlig overlevelsesfaktor per uke i salgsvinduet, hentet fra den
    # ubeskattede kurven.
    overlevelse = []
    for i in range(start_idx, c.n_weeks):
        forrige = c.weekly_count_alive[i - 1] if i > 0 else info["stocked"]
        overlevelse.append(c.weekly_count_alive[i] / forrige if forrige > 0 else 1.0)

    batcher = []
    bestand = c.weekly_count_alive[start_idx - 1] if start_idx > 0 else float(info["stocked"])
    for j in range(n):
        i = start_idx + j
        bestand *= overlevelse[j]  # naturlig dødelighet denne uken, FØR salg
        andel = 1.0 / (n - j)
        solgt_antall = bestand * andel
        vekt_kg = c.weekly_weight_kg[i]
        solgt_biomasse_kg = solgt_antall * vekt_kg
        bestand -= solgt_antall

        # KUN de "standing"-feltene oppdateres - representerer tankens
        # FAKTISKE stående bestand/biomasse ETTER denne ukens salg.
        c.weekly_standing_count[i] = bestand
        c.weekly_standing_biomass_kg[i] = bestand * vekt_kg

        batcher.append({
            "batch_id": f"{c.id}-B{j + 1}", "delivery_week": c.start_week + i,
            "delivered_count": solgt_antall, "delivered_biomass_kg": solgt_biomasse_kg,
            "avg_weight_kg": vekt_kg,
        })
    return batcher


def _weekly_snapshot(res: CohortResult, col: str, n_wk: int):
    """End-of-week value (day 7, 14, 21, ...) - matches how the rest of the
    model already samples weight/biomass, so counts/weights line up across
    all weekly tables."""
    out = []
    for wk in range(n_wk):
        day_idx = wk * 7 + 6
        row = res.daily.iloc[min(day_idx, len(res.daily) - 1)]
        out.append(row[col])
    return out


def _weekly_feed_sum(res: CohortResult, n_wk: int):
    """Feed is a FLOW (kg consumed that week), not a snapshot - so this
    sums the 7 days in each week rather than sampling one day, unlike
    weight/biomass/count above."""
    feed = res.daily["feed_kg_day"].to_numpy()
    out = []
    for wk in range(n_wk):
        start, end = wk * 7, min(wk * 7 + 7, len(feed))
        out.append(float(feed[start:end].sum()) if start < len(feed) else 0.0)
    return out


def _weekly_gross_growth_sum(res: CohortResult, n_wk: int):
    """Bruttovekst er, som fôr, en FLOW - summeres over ukens 7 dager, IKKE
    en uke-slutt-snapshot. Dette er populasjon FØR dødelighet x vekst per
    fisk hver dag - dvs. inkluderer veksten til fisk som dør samme uke, i
    motsetning til biomasse-differansen (som netter ut dødelighetstapet)."""
    gg = res.daily["gross_growth_kg_day"].to_numpy()
    out = []
    for wk in range(n_wk):
        start, end = wk * 7, min(wk * 7 + 7, len(gg))
        out.append(float(gg[start:end].sum()) if start < len(gg) else 0.0)
    return out


def build_1tank_schedule(cfg, growth_tables: GrowthTables | None = None):
    """
    Returns (weekly_df, generations, cohorts, meta).

    cfg må ha: TANK_VOLUME_M3, START_WEIGHT_KG, N_BATCHES_IN_ROTATION,
    BATCH_SMOLT_COUNTS, BATCH_GROWTH_WEEKS, BATCH_CLEANING_WEEKS,
    MAX_DENSITY_KG_M3, ANNUAL_MORTALITY_PCT, RGI_PCT, MONTHLY_TEMPERATURES_C,
    START_ISO_YEAR, START_ISO_WEEK, N_YEARS_TO_RUN.

    `cohorts` returneres (i tillegg til weekly_df/generations, som de andre
    schedulerne også returnerer) fordi resource_ledger.py trenger tilgang
    til hver kohorts daglige/ukentlige data (fôr, antall, biomasse) for å
    bygge ressursregnskapet - weekly_df alene har bare det som vises i
    kalender-illustrasjonen.
    """
    gt = growth_tables or GrowthTables(fcr_csv="data/fcr_table.csv", sgr_csv="data/sgr_table.csv")
    temp_fn = monthly_profile(cfg.MONTHLY_TEMPERATURES_C)
    daily_mort = daily_mortality_rate(cfg.ANNUAL_MORTALITY_PCT)

    n_batches = max(1, int(cfg.N_BATCHES_IN_ROTATION))
    smolt_list = [int(c) for c in cfg.BATCH_SMOLT_COUNTS[:n_batches]]
    growth_weeks_list = [max(1, int(w)) for w in cfg.BATCH_GROWTH_WEEKS[:n_batches]]
    cleaning_weeks_list = [max(0, int(w)) for w in cfg.BATCH_CLEANING_WEEKS[:n_batches]]
    sales_window_default = getattr(cfg, "BATCH_SALES_WINDOW_WEEKS", [1])
    sales_window_list = [max(1, int(w)) for w in (sales_window_default * n_batches)[:n_batches]]
    # Smoltvekt PER OPPSKRIFT - default til cfg.START_WEIGHT_KG for ALLE
    # oppskrifter hvis BATCH_START_WEIGHT_KG ikke er satt (bakoverkompatibelt
    # med config-er som kun har den gamle, globale START_WEIGHT_KG).
    start_weight_default = getattr(cfg, "BATCH_START_WEIGHT_KG", [cfg.START_WEIGHT_KG] * n_batches)
    start_weight_list = [float(w) for w in (start_weight_default * n_batches)[:n_batches]]
    # Ekstra venteuker FØR denne oppskriften starter, UTOVER dens egen
    # vasketid (default 0 = uendret oppførsel, tanken går rett fra én
    # oppskrifts vask til neste oppskrifts innsett, som før). Gjelder
    # HVER GANG denne oppskriften kommer opp i rotasjonen (ikke bare
    # første gang) - brukes til å gi en oppskrift en SENERE, fast
    # startdato enn "rett etter forrige oppskrift er ferdig vasket"
    # (kan ALDRI gjøre den TIDLIGERE - én tank kan ikke romme to
    # kohorter samtidig).
    ekstra_venteuker_default = getattr(cfg, "BATCH_EKSTRA_VENTEUKER", [0] * n_batches)
    ekstra_venteuker_list = [max(0, int(w)) for w in (ekstra_venteuker_default * n_batches)[:n_batches]]

    n_weeks_total = cfg.N_YEARS_TO_RUN * 52 + max(growth_weeks_list) + max(cleaning_weeks_list) + max(ekstra_venteuker_list) + 4

    def simulate_growth(stocked_count, n_wk, start_week_idx, start_weight_kg):
        start_date = monday_of_week(start_week_idx, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
        res = simulate_cohort(
            gt, start_weight_kg, stocked_count=stocked_count, duration_days=n_wk * 7,
            mortality_profile_fn=lambda d: daily_mort, start_date=start_date,
            temp_profile_fn=temp_fn, rgi_pct=cfg.RGI_PCT,
        )
        wb = _weekly_snapshot(res, "biomass_kg", n_wk)
        ww = _weekly_snapshot(res, "weight_kg", n_wk)
        wc = _weekly_snapshot(res, "count_alive", n_wk)
        wf = _weekly_feed_sum(res, n_wk)
        wg = _weekly_gross_growth_sum(res, n_wk)
        return res, wb, ww, wc, wf, wg

    generations = {}
    cohorts: list[Cohort] = []

    gen_n = 0
    w = 0
    while True:
        batch_idx = gen_n % n_batches
        w += ekstra_venteuker_list[batch_idx]  # ekstra ventetid FØR denne oppskriften, utover egen vasketid
        growth_w = growth_weeks_list[batch_idx]
        cleaning_w = cleaning_weeks_list[batch_idx]
        salgsvindu_uker = sales_window_list[batch_idx]
        if w + growth_w > n_weeks_total:
            break

        gen_n += 1
        gid = f"K{gen_n}"  # "K" for Kohort - IKKE "G" for Generasjon: en generasjon (øyerogn til
                            # settefiskanlegget) kan splittes på flere kohorter i sjø, som igjen
                            # splittes på batcher - "Generasjon" ville hoppet over ett ledd i hierarkiet.
        stocked = smolt_list[batch_idx]

        res, wb, ww, wc, wf, wg = simulate_growth(stocked, growth_w, w, start_weight_list[batch_idx])
        cohort = Cohort(gid, "tank1", w, growth_w, res, wb, ww, wc, wf, wg)
        cohorts.append(cohort)

        delivery_week = w + growth_w - 1

        generations[gid] = {
            "start_week": w,
            "delivery_week": delivery_week,
            "batch": batch_idx + 1,
            "growth_weeks": growth_w,
            "cleaning_weeks": cleaning_w,
            "sales_window_weeks": salgsvindu_uker,
            "stocked": stocked,
            "start_weight_kg": start_weight_list[batch_idx],
            "delivery_weight_kg": res.target_weight_kg,
            "survivors_at_delivery": res.surviving_count,
            "delivered_biomass_kg": res.final_biomass_kg,
            "total_feed_kg": res.total_feed_kg,
            "overall_fcr": res.overall_fcr,
            "total_gross_growth_kg": res.total_gross_growth_kg,
        }
        # Splitter de siste salgsvindu_uker vekstukene i batcher med avtakende
        # andel - oppdaterer weekly_standing_biomass_kg/weekly_standing_count
        # i disse ukene, slik at tankens stående bestand faktisk reduseres
        # etter hvert som batcher selges (i stedet for uendret til siste uke).
        generations[gid]["batches"] = _split_i_batcher(cohort, generations[gid], salgsvindu_uker)

        # Maks tetthet/"over tak" regnes FRA DEN FAKTISKE STÅENDE biomassen
        # (etter harvest-splitting) - IKKE fra res.final_biomass_kg (som ville
        # vist tettheten SOM OM all fisk fortsatt sto i tanken helt til siste
        # uke, selv om salgsvinduet allerede har begynt å redusere bestanden).
        # Uten dette blir "Maks tetthet"/"Over tak?" inkonsistent med grafens
        # stående biomasse-areal, som allerede bruker den riktige kurven.
        maks_stående_biomasse_kg = max(cohort.weekly_standing_biomass_kg)
        max_density = maks_stående_biomasse_kg / cfg.TANK_VOLUME_M3
        generations[gid]["max_density_kg_m3"] = max_density
        generations[gid]["tetthet_over_tak"] = max_density > cfg.MAX_DENSITY_KG_M3

        w = w + growth_w + cleaning_w

    full_rotasjon_uker = sum(growth_weeks_list[i] + cleaning_weeks_list[i] for i in range(n_batches))

    weekly_df = _assemble_wide_table(cfg, cohorts, generations, n_weeks_total)
    meta = {
        "n_batches_in_rotation": n_batches,
        "batch_smolt_counts": smolt_list,
        "batch_growth_weeks": growth_weeks_list,
        "batch_cleaning_weeks": cleaning_weeks_list,
        "full_rotasjon_uker": full_rotasjon_uker,
    }
    return weekly_df, generations, cohorts, meta


def _assemble_wide_table(cfg, cohorts, generations, n_weeks_total):
    labels, dates = [], []
    for w in range(n_weeks_total):
        lbl, d = week_label(w, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
        labels.append(lbl)
        dates.append(d)

    tank = {}
    for c in cohorts:
        for i in range(c.n_weeks):
            wk = c.start_week + i
            if wk >= n_weeks_total:
                break
            tank[wk] = {
                "kohort": c.id, "status": "Vekst",
                "biomasse_t": c.weekly_standing_biomass_kg[i] / 1000.0,
                "vekt_kg": c.weekly_weight_kg[i],
            }

    for gid, info in generations.items():
        dw = info["delivery_week"]
        for k in range(info["cleaning_weeks"]):
            wk = dw + 1 + k
            if wk < n_weeks_total and wk not in tank:
                tank[wk] = {"kohort": f"({gid})", "status": "Rengjoring", "biomasse_t": 0.0, "vekt_kg": None}

    levering_row, levert_row = {}, {}
    for gid, info in generations.items():
        for batch in info["batches"]:
            wk = batch["delivery_week"]
            levering_row[wk] = batch["batch_id"]
            levert_row[wk] = levert_row.get(wk, 0.0) + batch["delivered_biomass_kg"] / 1000.0

    rows = []
    kalenderuke = {"felt": "Kalenderuke", **dict(zip(labels, labels))}
    dato_row = {"felt": "Dato", **{lbl: d.isoformat() for lbl, d in zip(labels, dates)}}
    rows += [kalenderuke, dato_row]

    koh = {"felt": "Tank 1 - kohort"}
    stat = {"felt": "Tank 1 - status"}
    bio = {"felt": "Tank 1 - biomasse (t)"}
    dens = {"felt": "Tank 1 - tetthet (kg/m3)"}
    vekt = {"felt": "Tank 1 - vekt (g)"}
    for wk, lbl in enumerate(labels):
        cell = tank.get(wk)
        if cell is None:
            koh[lbl], stat[lbl], bio[lbl] = "", "Ledig", 0.0
            dens[lbl], vekt[lbl] = 0.0, None
        else:
            koh[lbl], stat[lbl], bio[lbl] = cell["kohort"], cell["status"], round(cell["biomasse_t"], 1)
            dens[lbl] = round(cell["biomasse_t"] * 1000 / cfg.TANK_VOLUME_M3, 1)
            vekt[lbl] = round(cell["vekt_kg"] * 1000, 1) if cell["vekt_kg"] is not None else None
    rows += [koh, stat, bio, dens, vekt]

    lev = {"felt": "Levering"}
    wfe = {"felt": "Levert WFE (t)"}
    akk = {"felt": "Akkumulert i aret (t)"}
    cum, cum_year = 0.0, None
    for wk, lbl in enumerate(labels):
        lev[lbl] = levering_row.get(wk, "")
        wfe[lbl] = round(levert_row.get(wk, 0.0), 1)
        iso_year = dates[wk].isocalendar()[0]
        if iso_year != cum_year:
            cum_year, cum = iso_year, 0.0
        cum += levert_row.get(wk, 0.0)
        akk[lbl] = round(cum, 1)
    rows += [lev, wfe, akk]

    return pd.DataFrame(rows).set_index("felt")


if __name__ == "__main__":
    import config_1tank as cfg
    df, gens, cohorts, meta = build_1tank_schedule(cfg)
    print("Full rotasjon (uker):", meta["full_rotasjon_uker"])
    for gid, info in list(gens.items())[:6]:
        print(gid, "batch", info["batch"], "levert", round(info["delivered_biomass_kg"] / 1000, 1), "t",
              "FCR", round(info["overall_fcr"], 3))
