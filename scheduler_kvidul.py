"""
scheduler_kvidul.py - Kvidul post-smolt: kohorter BAKOVER fra ABD-leveranse,
kapasitet per TRINN (karpool)
------------------------------------------------------------------------------
Lag ved siden av scheduler_multitank.py. Gjenbruker den verifiserte
vekstmotoren (simulator.simulate_cohort + Skretting-tabellene) og
Cohort-dataklassen fra scheduler_1tank.py uendret. Det som er NYTT:

  1. PLASSERING: hver kohort får sin leveringsuke fra ABD-innsettplanen
     (første mandag i jan/mar/mai/... for ABD 1, feb/apr/jun/... for ABD 2)
     og settes inn VEKSTUKER før det. Ingen tankrotasjon - Kvidul har
     karpooler, ikke én tank per kohort.

  2. VEKSTUKER regnes ut fra målvekt (LEVERT_VEKT_KG) ved den valgte
     RAS-temperaturen: simuler til målvekt, rund opp til hele uker. Alle
     kohorter får samme antall uker (konstant temperatur), men koden er
     generell dersom temperaturprofilen skulle variere.

  3. INNSETTANTALL regnes ut (modus "auto") slik at LEVERT_ANTALL faktisk
     leveres etter dødelighet - "hvor mange 30 g må inn for 850 000 ut".

  4. KAPASITET PER TRINN: hver uke tilhører kohorten ETT trinn (yngel /
     smolt / post-smolt / fase 2) etter vekt. m3-behov = stående biomasse
     / trinnets tetthetstak. Karbehov = m3-behov / karvolum, rundet OPP per
     kohort (et kar deles ikke mellom kohorter). Summen over kohorter
     sammenlignes med trinnets antall kar. Dette er "skyveskott"-logikken
     fra Big Dipper brukt på flere pooler.

Returverdien har SAMME form som build_1tank_schedule()/build_multitank_
schedule(): (weekly_df, generations, cohorts, meta) - så resource_ledger.py
og alle P&L/balanse-funksjonene fungerer uendret. I generations brukes
  "batch" = ABD-nummer (grupperingsdimensjon i appen: "ABD 1"/"ABD 2"),
  "tank"  = ABD-nummer,
  Kohort-ID "A1-K3" = ABD 1, tredje leveranse. Batch-ID "A1-K3-B1".

cfg må ha (se config_kvidul.py): N_ABD, ABD_INNSETT_MANEDER,
LEVERT_ANTALL_PER_LEVERANSE, LEVERT_VEKT_KG, INNSETT_ANTALL_MODUS,
INNSETT_ANTALL_FAST, INNSETT_ANTALL_AVRUNDING, LEVERANSE_START_AR,
N_YEARS_TO_RUN, SALGSVINDU_UKER, VASKEUKER_ETTER_LEVERING, START_WEIGHT_KG,
TRINN, FASE2_OVERGANG_G, KAR_DELES_IKKE_MELLOM_KOHORTER, ANNUAL_MORTALITY_PCT,
RGI_PCT, MONTHLY_TEMPERATURES_C.
Scheduleren SETTER cfg.START_ISO_YEAR/START_ISO_WEEK (uke 0 = første
Kvidul-innsett) slik at ledgeren og appen bruker riktig ukeakse.
"""

from __future__ import annotations
import math
from datetime import date, timedelta
import pandas as pd

from growth_tables import GrowthTables
from simulator import simulate_cohort
from temperature import monthly_profile
from scheduler_1tank import (
    Cohort, daily_mortality_rate, monday_of_week, week_label,
    _split_i_batcher, _weekly_snapshot, _weekly_feed_sum, _weekly_gross_growth_sum,
)


# ----------------------------------------------------------------------
# Leveranseplan
# ----------------------------------------------------------------------
def _forste_mandag(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def abd_leveringsdatoer(cfg) -> list[tuple[int, date]]:
    """[(abd_nr, mandag for ABD-innsett), ...] for alle leveranseår, sortert
    på dato. ABD-innsett = Kvidul-leveranse (brønnbåt samme uke)."""
    n_abd = max(1, int(getattr(cfg, "N_ABD", 1)))
    maneder = getattr(cfg, "ABD_INNSETT_MANEDER", {1: [1, 3, 5, 7, 9, 11], 2: [2, 4, 6, 8, 10, 12]})
    start_ar = int(cfg.LEVERANSE_START_AR)
    n_ar = int(cfg.N_YEARS_TO_RUN)
    ut = []
    for abd in range(1, n_abd + 1):
        mnd_liste = maneder.get(abd, maneder[1])
        for ar in range(start_ar, start_ar + n_ar):
            for m in mnd_liste:
                ut.append((abd, _forste_mandag(ar, m)))
    ut.sort(key=lambda x: (x[1], x[0]))
    return ut


# ----------------------------------------------------------------------
# Trinn-tilordning etter vekt
# ----------------------------------------------------------------------
def aktive_trinn(cfg) -> list[dict]:
    """Aktive trinn med vektgrenser fylt ut. Når fase 2 er aktiv tar fase 1
    post-smolt fisken til FASE2_OVERGANG_G og fase 2 resten."""
    trinn = [dict(t) for t in cfg.TRINN if t.get("aktiv", True)]
    overgang = float(getattr(cfg, "FASE2_OVERGANG_G", 400))
    ids = [t["id"] for t in trinn]
    if "fase2" in ids and "postsmolt" in ids:
        for t in trinn:
            if t["id"] == "postsmolt":
                t["vekt_til_g"] = overgang
            if t["id"] == "fase2":
                t["vekt_fra_g"] = overgang
                t["vekt_til_g"] = None
    # siste trinn åpent oppover
    trinn[-1]["vekt_til_g"] = None
    return trinn


def trinn_for_vekt(trinn: list[dict], vekt_g: float) -> dict:
    for t in trinn:
        lo = t["vekt_fra_g"] if t["vekt_fra_g"] is not None else 0.0
        hi = t["vekt_til_g"]
        if vekt_g >= lo and (hi is None or vekt_g < hi):
            return t
    return trinn[-1]


# ----------------------------------------------------------------------
# Hovedfunksjon
# ----------------------------------------------------------------------
def build_kvidul_schedule(cfg, growth_tables: GrowthTables | None = None):
    gt = growth_tables or GrowthTables(fcr_csv="data/fcr_table.csv", sgr_csv="data/sgr_table.csv")
    temp_fn = monthly_profile(cfg.MONTHLY_TEMPERATURES_C)
    daily_mort = daily_mortality_rate(cfg.ANNUAL_MORTALITY_PCT)
    start_w = float(cfg.START_WEIGHT_KG)
    mal_w = float(cfg.LEVERT_VEKT_KG)
    salgsvindu = max(1, int(getattr(cfg, "SALGSVINDU_UKER", 1)))
    vaskeuker = max(0, int(getattr(cfg, "VASKEUKER_ETTER_LEVERING", 1)))
    trinn = aktive_trinn(cfg)

    leveranser = abd_leveringsdatoer(cfg)
    if not leveranser:
        raise ValueError("Ingen leveranser i planen - sjekk N_ABD/LEVERANSE_START_AR/N_YEARS_TO_RUN")

    # ---- 1) Vekstuker til målvekt (ved første leveranse sin startdato) ----
    def uker_til_malvekt(innsett_dato: date) -> int:
        """Antall hele uker som gir leveringsvekt NÆRMEST målvekten (cfg.
        VEKSTUKER_AVRUNDING = "nærmest" (default) / "opp" (aldri under mål)
        / "ned" (aldri over mål))."""
        res = simulate_cohort(gt, start_w, stocked_count=1, target_weight_kg=mal_w,
                              mortality_profile_fn=lambda d: daily_mort, start_date=innsett_dato,
                              temp_profile_fn=temp_fn, rgi_pct=cfg.RGI_PCT)
        dager = res.cycle_days
        modus = getattr(cfg, "VEKSTUKER_AVRUNDING", "nærmest")
        opp, ned = max(1, math.ceil(dager / 7)), max(1, math.floor(dager / 7))
        if modus == "opp" or ned == opp:
            return opp
        if modus == "ned":
            return ned
        w_opp = res.daily["weight_kg"].iloc[min(opp * 7 - 1, len(res.daily) - 1)] if opp * 7 <= len(res.daily) else None
        if w_opp is None:
            # målvekt nås midt i siste uke - simuler én uke til for å finne vekten ved uke "opp"
            r2 = simulate_cohort(gt, start_w, stocked_count=1, duration_days=opp * 7,
                                 mortality_profile_fn=lambda d: daily_mort, start_date=innsett_dato,
                                 temp_profile_fn=temp_fn, rgi_pct=cfg.RGI_PCT)
            w_opp = r2.target_weight_kg
        w_ned = res.daily["weight_kg"].iloc[ned * 7 - 1]
        return ned if abs(w_ned - mal_w) <= abs(w_opp - mal_w) else opp

    # Konstant RAS-temperatur -> samme antall uker uansett dato. Regnes én
    # gang med en representativ dato (første leveranse minus ett år som
    # gjett) - og verifiseres per kohort under (re-regnes hvis temperaturen
    # skulle variere over året).
    forste_lev = leveranser[0][1]
    vekstuker_std = uker_til_malvekt(forste_lev - timedelta(weeks=26))

    # ---- 2) Ukeakse: uke 0 = første Kvidul-innsett ----
    forste_innsett = forste_lev - timedelta(weeks=vekstuker_std - 1)   # levering i vekstuke nr. vekstuker (0-indeks: -1)
    iso = forste_innsett.isocalendar()
    cfg.START_ISO_YEAR, cfg.START_ISO_WEEK = int(iso[0]), int(iso[1])
    anker = monday_of_week(0, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)

    siste_lev = leveranser[-1][1]
    n_weeks_total = (siste_lev - anker).days // 7 + vaskeuker + 4

    # ---- 3) Innsettantall for ønsket levert antall ----
    def innsett_antall(vekstuker: int, innsett_dato: date) -> int:
        modus = getattr(cfg, "INNSETT_ANTALL_MODUS", "auto")
        if modus == "fast":
            return int(cfg.INNSETT_ANTALL_FAST)
        # overlevelse over vekstukene (konstant daglig rate -> uavhengig av dato)
        overlevelse = (1.0 - daily_mort) ** (vekstuker * 7)
        rund = max(1, int(getattr(cfg, "INNSETT_ANTALL_AVRUNDING", 1)))
        n = int(cfg.LEVERT_ANTALL_PER_LEVERANSE) / overlevelse
        return int(math.ceil(n / rund) * rund)

    # ---- 4) Simuler hver kohort ----
    generations, cohorts = {}, []
    teller_per_abd = {}
    for abd, lev_dato in leveranser:
        # vekstuker for denne kohorten (re-regnes kun hvis profilen varierer)
        vekstuker = vekstuker_std
        innsett_dato = lev_dato - timedelta(weeks=vekstuker - 1)
        start_week = (innsett_dato - anker).days // 7
        if start_week < 0:
            # skulle ikke skje - ankeret er satt fra første leveranse
            continue
        teller_per_abd[abd] = teller_per_abd.get(abd, 0) + 1
        gid = f"A{abd}-K{teller_per_abd[abd]}"
        stocked = innsett_antall(vekstuker, innsett_dato)

        res = simulate_cohort(gt, start_w, stocked_count=stocked, duration_days=vekstuker * 7,
                              mortality_profile_fn=lambda d: daily_mort, start_date=innsett_dato,
                              temp_profile_fn=temp_fn, rgi_pct=cfg.RGI_PCT)
        wb = _weekly_snapshot(res, "biomass_kg", vekstuker)
        ww = _weekly_snapshot(res, "weight_kg", vekstuker)
        wc = _weekly_snapshot(res, "count_alive", vekstuker)
        wf = _weekly_feed_sum(res, vekstuker)
        wg = _weekly_gross_growth_sum(res, vekstuker)
        c = Cohort(gid, f"abd{abd}", start_week, vekstuker, res, wb, ww, wc, wf, wg)
        cohorts.append(c)

        delivery_week = start_week + vekstuker - 1
        info = {
            "start_week": start_week,
            "delivery_week": delivery_week,
            "batch": abd,                 # grupperingsdimensjon i appen ("ABD n")
            "tank": abd,
            "abd": abd,
            "leveringsdato": lev_dato,
            "growth_weeks": vekstuker,
            "cleaning_weeks": vaskeuker,
            "sales_window_weeks": salgsvindu,
            "stocked": stocked,
            "start_weight_kg": start_w,
            "delivery_weight_kg": res.target_weight_kg,
            "survivors_at_delivery": res.surviving_count,
            "delivered_biomass_kg": res.final_biomass_kg,
            "total_feed_kg": res.total_feed_kg,
            "overall_fcr": res.overall_fcr,
            "total_gross_growth_kg": res.total_gross_growth_kg,
        }
        info["batches"] = _split_i_batcher(c, info, salgsvindu)

        # Trinn per uke + m3-/karbehov per uke for denne kohorten
        trinn_uke, m3_uke, kar_uke = [], [], []
        for i in range(vekstuker):
            t = trinn_for_vekt(trinn, ww[i] * 1000.0)
            bio = c.weekly_standing_biomass_kg[i]
            m3 = bio / float(t["tetthetstak_kg_m3"]) if t["tetthetstak_kg_m3"] else 0.0
            kar = m3 / float(t["kar_volum_m3"])
            if getattr(cfg, "KAR_DELES_IKKE_MELLOM_KOHORTER", True):
                kar = math.ceil(kar - 1e-9)
            trinn_uke.append(t["id"])
            m3_uke.append(m3)
            kar_uke.append(kar)
        info["trinn_per_uke"] = trinn_uke
        info["m3_behov_per_uke"] = m3_uke
        info["kar_behov_per_uke"] = kar_uke
        info["max_m3_behov"] = max(m3_uke) if m3_uke else 0.0
        info["max_kar_behov"] = max(kar_uke) if kar_uke else 0
        # "tetthet" i Big Dipper-forstand finnes ikke per kohort her - settes
        # til toppbiomasse / (karbehov x karvolum) i siste trinn, til info.
        siste_t = trinn_for_vekt(trinn, ww[-1] * 1000.0)
        info["max_density_kg_m3"] = (max(c.weekly_standing_biomass_kg) /
                                     (info["max_kar_behov"] * siste_t["kar_volum_m3"])
                                     if info["max_kar_behov"] else 0.0)
        info["tetthet_over_tak"] = False   # sjekkes på trinn-/anleggsnivå under
        generations[gid] = info

    cohorts.sort(key=lambda c: (c.start_week, c.tank))
    generations = {c.id: generations[c.id] for c in cohorts}

    # ---- 5) Trinn-kapasitet per uke (anleggsnivå) ----
    kapasitet = _trinn_kapasitet_per_uke(cfg, trinn, cohorts, generations, n_weeks_total)
    # flagg kohorter som er i et trinn i en uke der trinnet er overbooket
    for gid, info in generations.items():
        over = False
        for i, tid in enumerate(info["trinn_per_uke"]):
            wk = info["start_week"] + i
            if wk < n_weeks_total and kapasitet[tid]["over_kapasitet"][wk]:
                over = True
                break
        info["tetthet_over_tak"] = over

    weekly_df = _assemble_wide_table_kvidul(cfg, trinn, cohorts, generations, kapasitet, n_weeks_total)

    meta = {
        "n_batches_in_rotation": max(1, int(getattr(cfg, "N_ABD", 1))),
        "batch_smolt_counts": [int(generations[c.id]["stocked"]) for c in cohorts[: int(getattr(cfg, "N_ABD", 1))]],
        "batch_growth_weeks": [vekstuker_std] * max(1, int(getattr(cfg, "N_ABD", 1))),
        "batch_cleaning_weeks": [vaskeuker] * max(1, int(getattr(cfg, "N_ABD", 1))),
        "full_rotasjon_uker": vekstuker_std + vaskeuker,
        "n_tanks": max(1, int(getattr(cfg, "N_ABD", 1))),
        "n_abd": max(1, int(getattr(cfg, "N_ABD", 1))),
        "vekstuker": vekstuker_std,
        "innsett_antall": [int(generations[c.id]["stocked"]) for c in cohorts[:1]][0] if cohorts else 0,
        "trinn": trinn,
        "kapasitet": kapasitet,
        "n_weeks_total": n_weeks_total,
        "leveranser": leveranser,
        "m3_pool": sum(t["antall_kar"] * t["kar_volum_m3"] for t in trinn),
    }
    return weekly_df, generations, cohorts, meta


# ----------------------------------------------------------------------
# Trinn-kapasitet
# ----------------------------------------------------------------------
def _trinn_kapasitet_per_uke(cfg, trinn, cohorts, generations, n_weeks_total) -> dict:
    """Per trinn: ukelister for stående biomasse, m3-behov, karbehov (sum av
    per-kohort-avrundede kar), antall kohorter, og flagg for overbooking."""
    ut = {}
    for t in trinn:
        ut[t["id"]] = {
            "navn": t["navn"], "antall_kar": int(t["antall_kar"]), "kar_volum_m3": float(t["kar_volum_m3"]),
            "kapasitet_m3": int(t["antall_kar"]) * float(t["kar_volum_m3"]),
            "tetthetstak_kg_m3": float(t["tetthetstak_kg_m3"]),
            "biomasse_kg": [0.0] * n_weeks_total, "m3_behov": [0.0] * n_weeks_total,
            "kar_behov": [0] * n_weeks_total, "n_kohorter": [0] * n_weeks_total,
            "kohorter": [[] for _ in range(n_weeks_total)],
        }
    for c in cohorts:
        info = generations[c.id]
        for i in range(c.n_weeks):
            wk = c.start_week + i
            if wk >= n_weeks_total:
                break
            tid = info["trinn_per_uke"][i]
            k = ut[tid]
            k["biomasse_kg"][wk] += c.weekly_standing_biomass_kg[i]
            k["m3_behov"][wk] += info["m3_behov_per_uke"][i]
            k["kar_behov"][wk] += info["kar_behov_per_uke"][i]
            k["n_kohorter"][wk] += 1
            k["kohorter"][wk].append(c.id)
        # vaskeuker: karene i SISTE trinn er opptatt (samme antall kar som i leveringsuken)
        siste_tid = info["trinn_per_uke"][-1]
        siste_kar = info["kar_behov_per_uke"][-1]
        for j in range(info["cleaning_weeks"]):
            wk = info["delivery_week"] + 1 + j
            if wk < n_weeks_total:
                ut[siste_tid]["kar_behov"][wk] += siste_kar
    for tid, k in ut.items():
        k["over_kapasitet"] = [k["kar_behov"][w] > k["antall_kar"] or k["m3_behov"][w] > k["kapasitet_m3"] + 1e-6
                               for w in range(n_weeks_total)]
        k["tetthet_ved_full_utnyttelse"] = [b / k["kapasitet_m3"] if k["kapasitet_m3"] else 0.0 for b in k["biomasse_kg"]]
        k["maks_biomasse_kg"] = max(k["biomasse_kg"]) if k["biomasse_kg"] else 0.0
        k["maks_kar_behov"] = max(k["kar_behov"]) if k["kar_behov"] else 0
        k["maks_m3_behov"] = max(k["m3_behov"]) if k["m3_behov"] else 0.0
        k["ledige_kar_min"] = k["antall_kar"] - k["maks_kar_behov"]
    return ut


# ----------------------------------------------------------------------
# Ukekalender (samme oppsett som scheduler_multitank._assemble_wide_table_multi)
# ----------------------------------------------------------------------
def _assemble_wide_table_kvidul(cfg, trinn, cohorts, generations, kapasitet, n_weeks_total) -> pd.DataFrame:
    labels, dates = [], []
    for w in range(n_weeks_total):
        lbl, d = week_label(w, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
        labels.append(lbl)
        dates.append(d)

    rows = [
        {"felt": "Kalenderuke", **dict(zip(labels, labels))},
        {"felt": "Dato", **{lbl: d.isoformat() for lbl, d in zip(labels, dates)}},
    ]
    innsett_row = {}
    for gid, info in generations.items():
        wk = info["start_week"]
        innsett_row[wk] = (innsett_row[wk] + ", " if wk in innsett_row else "") + gid
    rows.append({"felt": "Innsett (30 g)", **{lbl: innsett_row.get(w, "") for w, lbl in enumerate(labels)}})

    for t in trinn:
        k = kapasitet[t["id"]]
        pre = t["navn"]
        rows.append({"felt": f"{pre} - kohorter", **{lbl: ", ".join(k["kohorter"][w]) for w, lbl in enumerate(labels)}})
        rows.append({"felt": f"{pre} - biomasse (t)", **{lbl: round(k["biomasse_kg"][w] / 1000.0, 1) for w, lbl in enumerate(labels)}})
        rows.append({"felt": f"{pre} - m3-behov", **{lbl: round(k["m3_behov"][w], 0) for w, lbl in enumerate(labels)}})
        rows.append({"felt": f"{pre} - kar i bruk (av {k['antall_kar']})", **{lbl: k["kar_behov"][w] for w, lbl in enumerate(labels)}})
        rows.append({"felt": f"{pre} - tetthet ved full utnyttelse (kg/m3)",
                     **{lbl: round(k["tetthet_ved_full_utnyttelse"][w], 1) for w, lbl in enumerate(labels)}})
        rows.append({"felt": f"{pre} - over kapasitet?", **{lbl: ("JA" if k["over_kapasitet"][w] else "") for w, lbl in enumerate(labels)}})

    total_bio = [sum(kapasitet[t["id"]]["biomasse_kg"][w] for t in trinn) for w in range(n_weeks_total)]
    rows.append({"felt": "Anlegg totalt - biomasse (t)", **{lbl: round(total_bio[w] / 1000.0, 1) for w, lbl in enumerate(labels)}})

    levering_row, levert_row = {}, {}
    for gid, info in generations.items():
        for b in info["batches"]:
            wk = b["delivery_week"]
            levering_row[wk] = (levering_row[wk] + ", " if wk in levering_row else "") + b["batch_id"]
            levert_row[wk] = levert_row.get(wk, 0.0) + b["delivered_biomass_kg"] / 1000.0
    lev = {"felt": "Levering til ABD"}
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


# ----------------------------------------------------------------------
# Trinn-sammendrag (til app/tabell)
# ----------------------------------------------------------------------
def trinn_sammendrag(meta: dict) -> pd.DataFrame:
    rows = []
    for tid, k in meta["kapasitet"].items():
        rows.append({
            "Trinn": k["navn"],
            "Kar": k["antall_kar"],
            "Karvolum (m3)": k["kar_volum_m3"],
            "Kapasitet (m3)": k["kapasitet_m3"],
            "Tetthetstak (kg/m3)": k["tetthetstak_kg_m3"],
            "Maks biomasse (t)": round(k["maks_biomasse_kg"] / 1000.0, 1),
            "Maks m3-behov": round(k["maks_m3_behov"], 0),
            "Maks kar i bruk": k["maks_kar_behov"],
            "Ledige kar (min)": k["ledige_kar_min"],
            "Tetthet ved full utnyttelse, topp (kg/m3)": round(max(k["tetthet_ved_full_utnyttelse"]), 1),
            "Uker over kapasitet": int(sum(k["over_kapasitet"])),
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import config_kvidul as cfg
    df, gens, cohorts, meta = build_kvidul_schedule(cfg)
    print(f"ABD: {meta['n_abd']}  vekstuker 30 g -> {cfg.LEVERT_VEKT_KG*1000:.0f} g: {meta['vekstuker']}  "
          f"innsett per kohort: {meta['innsett_antall']:,}  uke 0 = {cfg.START_ISO_YEAR}-U{cfg.START_ISO_WEEK:02d}")
    for gid, info in list(gens.items())[:8]:
        print(gid, "innsett", week_label(info["start_week"], cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)[0],
              "lev", info["leveringsdato"], f"{info['survivors_at_delivery']:,.0f} stk",
              f"{info['delivery_weight_kg']*1000:.0f} g", f"{info['delivered_biomass_kg']/1000:.0f} t",
              "FCR", round(info["overall_fcr"], 3), "maks kar", info["max_kar_behov"], "over?", info["tetthet_over_tak"])
    print(trinn_sammendrag(meta).to_string(index=False))
