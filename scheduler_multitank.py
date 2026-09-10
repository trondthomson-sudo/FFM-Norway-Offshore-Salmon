"""
scheduler_multitank.py - N PARALLELLE tanker (Aqualoop Big Dipper), forskjøvet innsett
------------------------------------------------------------------------------
Lag OPPÅ scheduler_1tank.py - selve vekst-/batch-motoren (build_1tank_schedule,
_split_i_batcher, simulate_cohort) røres IKKE. Denne modulen kjører den
verifiserte 1-tank-scheduleren én gang PER TANK, med startuken forskjøvet
TANK_STAGGER_WEEKS uker per tank (tank 1 i uke 0, tank 2 i uke 8, tank 3 i
uke 16, osv. - "uke 1 + 8 + 8 + 8 ..."), og slår resultatene sammen på ÉN
felles ukeakse (uke 0 = tank 1 sitt første innsett = cfg.START_ISO_YEAR/WEEK).

Hver tank har SIN EGEN oppskrift (smoltvekt, smoltantall, vekstuker,
vaskeuker, salgsvindu) = element t i cfg.BATCH_*-listene - tankene settes
inn i ulike sesonger og kan trenge ulik fisk/antall. Hver tank kjører sin
egen, uavhengige rotasjon (samme oppskrift gjentatt: vekst + vask, ...)
og kan aldri kollidere med de andre.

Resultatet har NØYAKTIG samme form som build_1tank_schedule() returnerer
(weekly_df, generations, cohorts, meta), slik at resource_ledger.py og hele
Streamlit-appen fungerer uendret nedstrøms:
  - Kohort-ID: "G1-K1", "G1-K2", ..., "G1-K6", "G2-K1", ... = Generasjon n
    (n-te runde innsett i anlegget), Kohort t (t-te utsett i runden = tank t).
    Batch-ID: "G1-K1-B1" ... "-B8".
  - generations[gid]["batch"] settes til TANKNUMMERET (1..N). Appen bruker
    dette feltet som grupperingsdimensjon ("Oppskrift N") i Samlet oversikt,
    massebalanse og kohort-sammendrag - i multi-tank-modus betyr det altså
    "Tank N", og appen bytter etikett deretter.
  - generations[gid]["tank"] = tanknummer (nytt felt, for tydelighet).
  - Cohort.tank = "tank1".."tankN".

cfg må i tillegg til det build_1tank_schedule krever ha:
  N_TANKS (int, 1-8) og TANK_STAGGER_WEEKS (int, uker mellom innsett).
Med N_TANKS = 1 delegeres det rett til build_1tank_schedule (uendret ID-er,
uendret oppførsel) - så den eksisterende 1-tank-visningen er intakt.
"""

from __future__ import annotations
from datetime import timedelta
import pandas as pd

from growth_tables import GrowthTables
from scheduler_1tank import build_1tank_schedule, week_label, monday_of_week


class _CfgKopi:
    pass


def _cfg_kopi(cfg):
    """Overfladisk kopi av alle (ikke-private) attributter - fungerer både
    for en modul (config_1tank) og et vanlig objekt (appens RunConfig)."""
    sub = _CfgKopi()
    for k in dir(cfg):
        if not k.startswith("_"):
            setattr(sub, k, getattr(cfg, k))
    return sub


def tank_start_weeks(cfg) -> list[int]:
    """Globale ukeindekser (uke 0 = tank 1) for FØRSTE innsett i hver tank."""
    n_tanks = max(1, int(getattr(cfg, "N_TANKS", 1)))
    eksplisitt = getattr(cfg, "TANK_START_WEEK_OFFSETS", None)
    if eksplisitt:
        # Eksplisitte startuker per tank (f.eks. første mandag i jan/mar/mai/
        # jul/sep/nov) - trumfer fast stagger. Kortere liste fylles ut.
        offs = [int(x) for x in eksplisitt][:n_tanks]
        stagger = max(0, int(getattr(cfg, "TANK_STAGGER_WEEKS", 8)))
        while len(offs) < n_tanks:
            offs.append(offs[-1] + stagger if offs else 0)
        return offs
    stagger = max(0, int(getattr(cfg, "TANK_STAGGER_WEEKS", 0)))
    return [t * stagger for t in range(n_tanks)]


def build_multitank_schedule(cfg, growth_tables: GrowthTables | None = None):
    n_tanks = max(1, int(getattr(cfg, "N_TANKS", 1)))
    if n_tanks == 1:
        weekly_df, generations, cohorts, meta = build_1tank_schedule(cfg, growth_tables)
        for info in generations.values():
            info["tank"] = 1
        meta["n_tanks"] = 1
        meta["tank_start_weeks"] = [0]
        return weekly_df, generations, cohorts, meta

    gt = growth_tables or GrowthTables(fcr_csv="data/fcr_table.csv", sgr_csv="data/sgr_table.csv")
    offsets = tank_start_weeks(cfg)

    # Global horisont: samme som 1-tank-scheduleren bruker for tank 1, slik
    # at ALLE tanker klippes ved samme absolutte uke (en tank som starter
    # 40 uker senere får ikke 40 uker ekstra simulering på slutten).
    def _per_tank(lst, t, default):
        """Oppskriftsverdi for tank t (0-indeksert): element t i listen, ellers
        siste element (så en kortere liste gjelder for resten av tankene)."""
        lst = list(lst) if lst is not None else []
        if not lst:
            return default
        return lst[t] if t < len(lst) else lst[-1]

    growth_max = max(int(_per_tank(cfg.BATCH_GROWTH_WEEKS, t, 50)) for t in range(n_tanks))
    clean_max = max(int(_per_tank(cfg.BATCH_CLEANING_WEEKS, t, 1)) for t in range(n_tanks))
    n_weeks_total = int(cfg.N_YEARS_TO_RUN) * 52 + growth_max + clean_max + 4

    all_cohorts, all_gens = [], {}
    per_tank_meta = None
    rotasjon_per_tank = []
    for t, offset in enumerate(offsets):
        tank_nr = t + 1
        sub = _cfg_kopi(cfg)
        # ÉN oppskrift per tank: tank t bruker element t i BATCH_*-listene
        # (smoltvekt, antall, vekstuker, vaskeuker, salgsvindu). Tankens
        # neste kohort gjentar samme oppskrift (N_BATCHES_IN_ROTATION = 1).
        sub.N_BATCHES_IN_ROTATION = 1
        sub.BATCH_SMOLT_COUNTS = [int(_per_tank(cfg.BATCH_SMOLT_COUNTS, t, 0))]
        sub.BATCH_GROWTH_WEEKS = [int(_per_tank(cfg.BATCH_GROWTH_WEEKS, t, 50))]
        sub.BATCH_CLEANING_WEEKS = [int(_per_tank(cfg.BATCH_CLEANING_WEEKS, t, 1))]
        sub.BATCH_SALES_WINDOW_WEEKS = [int(_per_tank(getattr(cfg, "BATCH_SALES_WINDOW_WEEKS", [1]), t, 1))]
        sub.BATCH_START_WEIGHT_KG = [float(_per_tank(getattr(cfg, "BATCH_START_WEIGHT_KG", [cfg.START_WEIGHT_KG]), t, cfg.START_WEIGHT_KG))]
        sub.START_WEIGHT_KG = sub.BATCH_START_WEIGHT_KG[0]
        # Forskyv kalenderankeret: denne tankens "uke 0" er tank 1 sin uke
        # `offset`. monday_of_week håndterer år-overgang via timedelta, og
        # vi henter ISO-år/-uke tilbake fra den faktiske datoen.
        d0 = monday_of_week(offset, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
        iso = d0.isocalendar()
        sub.START_ISO_YEAR, sub.START_ISO_WEEK = int(iso[0]), int(iso[1])
        # Ekstra venteuker gjelder per rotasjon i 1-tank-scheduleren - skal
        # IKKE brukes her (forskyvningen ligger i ankeret over).
        sub.BATCH_EKSTRA_VENTEUKER = [0]

        _, gens_t, cohorts_t, meta_t = build_1tank_schedule(sub, gt)
        per_tank_meta = per_tank_meta or meta_t
        rotasjon_per_tank.append(meta_t["full_rotasjon_uker"])

        for c in cohorts_t:
            gid_local = c.id
            info = gens_t[gid_local]
            g_start = info["start_week"] + offset
            if g_start + info["growth_weeks"] > n_weeks_total:
                continue  # utenfor felles horisont
            # ID: G{n}-K{t} = Generasjon n (n-te runde med innsett i anlegget),
            # Kohort t (t-te utsett i runden = tank t). Tank 1 sin første
            # kohort er G1-K1, tank 2 sin første er G1-K2, ... tank 1 sin
            # andre kohort er G2-K1, osv. Batch: G1-K1-B1 .. -B8.
            gen_nr = int(gid_local.replace("K", ""))
            gid = f"G{gen_nr}-K{tank_nr}"
            c.id = gid
            c.tank = f"tank{tank_nr}"
            c.start_week = g_start
            info["start_week"] = g_start
            info["delivery_week"] = info["delivery_week"] + offset
            info["batch"] = tank_nr          # grupperingsdimensjon i appen ("Tank N")
            info["tank"] = tank_nr
            for b in info["batches"]:
                b["delivery_week"] += offset
                b["batch_id"] = b["batch_id"].replace(f"{gid_local}-", f"{gid}-", 1)
            # FLEKSIBLE KAKESTYKKER (bekreftet av bruker): veggene i Big Dipper
            # kan flyttes, så hver kohort får akkurat det volumet biomassen
            # krever ved tetthetstaket (m3-behov = biomasse / MAX_DENSITY).
            # Taket gjelder derfor IKKE per fast tank (83 333 m3), men for
            # ANLEGGET SAMLET: sum m3-behov over alle kohorter <= N_TANKS x
            # TANK_VOLUME_M3 (500 000 m3). Per-kohort-flagget nulles her -
            # anleggsnivå-sjekken gjøres i appen fra m3-behovskurvene.
            maks_bio = max(c.weekly_standing_biomass_kg) if c.weekly_standing_biomass_kg else 0.0
            info["max_m3_behov"] = maks_bio / float(cfg.MAX_DENSITY_KG_M3) if cfg.MAX_DENSITY_KG_M3 else 0.0
            if not bool(getattr(cfg, "SKOTT_FASTE", False)):
                info["tetthet_over_tak"] = False   # skyveskott: sjekkes på anleggsnivå i appen
            # (faste skott: 1-tank-motorens per-tank-flagg beholdes som det er)
            all_cohorts.append(c)
            all_gens[gid] = info

    # Sorter kronologisk (innsettuke, deretter tank) - appen antar at
    # generations/cohorts er i tidsrekkefølge (K1 er "første kohort").
    all_cohorts.sort(key=lambda c: (c.start_week, c.tank))
    all_gens = {c.id: all_gens[c.id] for c in all_cohorts}

    weekly_df = _assemble_wide_table_multi(cfg, all_cohorts, all_gens, n_weeks_total, n_tanks)
    meta = dict(per_tank_meta or {})
    meta["n_batches_in_rotation"] = n_tanks           # én oppskrift per tank
    meta["batch_smolt_counts"] = [int(_per_tank(cfg.BATCH_SMOLT_COUNTS, t, 0)) for t in range(n_tanks)]
    meta["batch_growth_weeks"] = [int(_per_tank(cfg.BATCH_GROWTH_WEEKS, t, 50)) for t in range(n_tanks)]
    meta["batch_cleaning_weeks"] = [int(_per_tank(cfg.BATCH_CLEANING_WEEKS, t, 1)) for t in range(n_tanks)]
    meta["rotasjon_per_tank"] = rotasjon_per_tank
    meta["n_tanks"] = n_tanks
    meta["m3_pool"] = float(cfg.TANK_VOLUME_M3) * n_tanks   # samlet fleksibelt volum i anlegget
    meta["tank_start_weeks"] = offsets
    meta["tank_stagger_weeks"] = int(getattr(cfg, "TANK_STAGGER_WEEKS", 0))
    return weekly_df, all_gens, all_cohorts, meta


def _assemble_wide_table_multi(cfg, cohorts, generations, n_weeks_total, n_tanks):
    """Samme oppsett som scheduler_1tank._assemble_wide_table, men én blokk
    rader per tank + en "Anlegg totalt"-blokk (sum biomasse, snitt-tetthet
    over ALLE tanker, levert)."""
    labels, dates = [], []
    for w in range(n_weeks_total):
        lbl, d = week_label(w, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
        labels.append(lbl)
        dates.append(d)

    tank_cells = {t: {} for t in range(1, n_tanks + 1)}
    for c in cohorts:
        t = int(c.tank.replace("tank", ""))
        for i in range(c.n_weeks):
            wk = c.start_week + i
            if wk >= n_weeks_total:
                break
            tank_cells[t][wk] = {
                "kohort": c.id, "status": "Vekst",
                "biomasse_t": c.weekly_standing_biomass_kg[i] / 1000.0,
                "vekt_kg": c.weekly_weight_kg[i],
            }
    for gid, info in generations.items():
        t = info["tank"]
        dw = info["delivery_week"]
        for k in range(info["cleaning_weeks"]):
            wk = dw + 1 + k
            if wk < n_weeks_total and wk not in tank_cells[t]:
                tank_cells[t][wk] = {"kohort": f"({gid})", "status": "Rengjoring", "biomasse_t": 0.0, "vekt_kg": None}

    levering_row, levert_row = {}, {}
    for gid, info in generations.items():
        for batch in info["batches"]:
            wk = batch["delivery_week"]
            levering_row[wk] = (levering_row[wk] + ", " if wk in levering_row else "") + batch["batch_id"]
            levert_row[wk] = levert_row.get(wk, 0.0) + batch["delivered_biomass_kg"] / 1000.0

    rows = [
        {"felt": "Kalenderuke", **dict(zip(labels, labels))},
        {"felt": "Dato", **{lbl: d.isoformat() for lbl, d in zip(labels, dates)}},
    ]
    total_bio = {lbl: 0.0 for lbl in labels}
    for t in range(1, n_tanks + 1):
        koh = {"felt": f"Tank {t} - kohort"}
        stat = {"felt": f"Tank {t} - status"}
        bio = {"felt": f"Tank {t} - biomasse (t)"}
        dens = {"felt": f"Tank {t} - tetthet (kg/m3)"}
        vekt = {"felt": f"Tank {t} - vekt (g)"}
        for wk, lbl in enumerate(labels):
            cell = tank_cells[t].get(wk)
            if cell is None:
                koh[lbl], stat[lbl], bio[lbl], dens[lbl], vekt[lbl] = "", "Ledig", 0.0, 0.0, None
            else:
                koh[lbl], stat[lbl], bio[lbl] = cell["kohort"], cell["status"], round(cell["biomasse_t"], 1)
                dens[lbl] = round(cell["biomasse_t"] * 1000 / cfg.TANK_VOLUME_M3, 1)
                vekt[lbl] = round(cell["vekt_kg"] * 1000, 1) if cell["vekt_kg"] is not None else None
                total_bio[lbl] += cell["biomasse_t"]
        rows += [koh, stat, bio, dens, vekt]

    tot_bio = {"felt": "Anlegg totalt - biomasse (t)", **{lbl: round(v, 1) for lbl, v in total_bio.items()}}
    tot_dens = {"felt": "Anlegg totalt - tetthet (kg/m3, snitt)",
                **{lbl: round(v * 1000 / (cfg.TANK_VOLUME_M3 * n_tanks), 1) for lbl, v in total_bio.items()}}
    rows += [tot_bio, tot_dens]

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
    cfg.N_TANKS, cfg.TANK_STAGGER_WEEKS = 6, 8
    df, gens, cohorts, meta = build_multitank_schedule(cfg)
    print("Tanker:", meta["n_tanks"], "startuker:", meta["tank_start_weeks"], "rotasjon/tank:", meta["full_rotasjon_uker"])
    for gid, info in list(gens.items())[:8]:
        print(gid, "start", info["start_week"], "lev", info["delivery_week"],
              round(info["delivered_biomass_kg"] / 1000, 1), "t", round(info["max_density_kg_m3"], 1), "kg/m3")
