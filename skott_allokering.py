"""
skott_allokering.py - FASTE SKOTT med splitting til et ledig skott
------------------------------------------------------------------------------
Lag OPPÅ scheduler_multitank.py (vekstmotoren røres ikke). Brukes når
"Kakestykker" = "Faste skott":

  * Anlegget har N_SKOTT faste skott à TANK_VOLUME_M3 (f.eks. 8 x 62 500 m3).
  * Det settes inn N_TANKS kohorter per år (f.eks. 6: første mandag i jan,
    mar, mai, jul, sep, nov) - "tank t" i multitank-scheduleren er her
    INNSETT-plass t (én oppskrift per innsett), ikke et fysisk skott.
  * Hver kohort settes inn i ett ledig skott (skott A). Når stående biomasse
    ville passert tetthetstaket (MAX_DENSITY_KG_M3 x skottvolum), splittes
    halvparten av fisken over i et ledig skott (skott B). Slakting tas fra
    skott B først; når B er tomt, vaskes det (vaskeuker) og er ledig igjen.
    Skott A vaskes etter siste batch, som før.
  * Skottene tildeles fortløpende (laveste ledige nummer). Et skott kan
    aldri romme to kohorter samtidig.

Funksjoner:
  alloker_skott(cfg, generations, cohorts, meta, weekly_df)
      - legger split-/skottinfo og tetthet PER SKOTT inn i generations,
        skottbelegg/-biomasse i meta og skottrader i weekly_df.
  optimaliser_smolt(cfg, generations, cohorts)
      - finner smoltantallet per innsett som gir HØYEST årlig levert biomasse
        med tetthet <= tak i hvert skott og aldri flere enn N_SKOTT skott i
        bruk samtidig. Utnytter at biomasse er lineær i antall fisk (samme
        per-fisk-kurver som siste kjøring).
"""

from __future__ import annotations
import math

import numpy as np
import pandas as pd

from scheduler_1tank import week_label

_EPS = 1e-9


# ----------------------------------------------------------------------
# Per-kohort: split-uke, frigjøring av skott B, fisk i A/B per uke
# ----------------------------------------------------------------------
def _fordeling(sb, sc, ac, w, split_idx):
    """Antall fisk i skott A og B per vekstuke gitt split i uke split_idx
    (None = ingen split). sb/sc/ac/w: stående biomasse, stående antall,
    ubeskattet antall (kun naturlig dødelighet), vekt - per uke i kohorten.
    Returnerer (A_cnt, B_cnt, release_idx) - release_idx = siste uke B er i
    bruk (fisk i B ved ukens start), None uten split."""
    n = len(sc)
    if split_idx is None:
        return list(sc), [0.0] * n, None
    s = split_idx
    ref_sc = sc[s - 1] if s > 0 else sc[0]
    ref_ac = ac[s - 1] if s > 0 else ac[0]
    A, B = [0.0] * n, [0.0] * n
    release = n - 1
    released = False
    for i in range(n):
        if i < s:
            A[i] = sc[i]
            continue
        halv = 0.5 * ref_sc * (ac[i] / ref_ac if ref_ac > 0 else 0.0)   # halvparten, naturlig dødelighet
        A[i] = min(sc[i], halv)
        B[i] = max(0.0, sc[i] - A[i])
        if not released and B[i] <= _EPS * max(1.0, ref_sc):
            release, released = i, True
    return A, B, release


def _kohort_plan(c, info, cap_kg, splitt):
    """Split-uke (relativ), frigjøring av B (relativ), fisk i A/B, maks tetthet."""
    sb = list(c.weekly_standing_biomass_kg)
    sc = list(c.weekly_standing_count)
    ac = list(c.weekly_count_alive)
    w = list(c.weekly_weight_kg)
    split = None
    if splitt:
        split = next((i for i, b in enumerate(sb) if b > cap_kg * (1 + 1e-9)), None)
        if split == 0:
            split = None   # over taket allerede ved innsett - splitting hjelper ikke
    A, B, rel = _fordeling(sb, sc, ac, w, split)
    return {"split": split, "release": rel, "A": A, "B": B, "w": w}


# ----------------------------------------------------------------------
# Tildeling av skott
# ----------------------------------------------------------------------
def alloker_skott(cfg, generations, cohorts, meta, weekly_df):
    n_skott = int(getattr(cfg, "N_SKOTT", getattr(cfg, "N_TANKS", 1)))
    V = float(cfg.TANK_VOLUME_M3)
    cap = float(cfg.MAX_DENSITY_KG_M3)
    splitt = bool(getattr(cfg, "SKOTT_SPLITT", True))
    n_uker = len(weekly_df.columns)

    # Intervaller (global uke, inkl. vask): (start, slutt, kohort, rolle)
    intervaller = []
    planer = {}
    for c in cohorts:
        info = generations[c.id]
        p = _kohort_plan(c, info, cap * V, splitt)
        planer[c.id] = p
        cw = int(info["cleaning_weeks"])
        s0 = c.start_week
        intervaller.append((s0, s0 + c.n_weeks - 1 + cw, c.id, "A"))
        if p["split"] is not None:
            intervaller.append((s0 + p["split"], s0 + p["release"] + cw, c.id, "B"))
    # A før B ved lik start, så en kohort alltid får "sitt" skott først
    intervaller.sort(key=lambda x: (x[0], x[3], x[2]))

    ledig_fra = [0] * n_skott           # første uke skottet er ledig
    ekstra = []                          # "virtuelle" skott ved overbooking
    tildelt = {}                         # (kohort, rolle) -> skottnr (1-basert)
    for start, slutt, gid, rolle in intervaller:
        nr = next((k for k in range(n_skott) if ledig_fra[k] <= start), None)
        if nr is not None:
            ledig_fra[nr] = slutt + 1
            tildelt[(gid, rolle)] = nr + 1
        else:
            k2 = next((k for k in range(len(ekstra)) if ekstra[k] <= start), None)
            if k2 is None:
                ekstra.append(0)
                k2 = len(ekstra) - 1
            ekstra[k2] = slutt + 1
            tildelt[(gid, rolle)] = n_skott + k2 + 1   # > n_skott = MANGLER skott

    n_tot = n_skott + len(ekstra)
    belegg = np.zeros(n_uker, dtype=int)
    skott_kohort = [[""] * n_uker for _ in range(n_tot)]
    skott_status = [["Ledig"] * n_uker for _ in range(n_tot)]
    skott_bio = [[0.0] * n_uker for _ in range(n_tot)]
    for start, slutt, gid, rolle in intervaller:
        for wk in range(max(0, start), min(n_uker, slutt + 1)):
            belegg[wk] += 1

    for c in cohorts:
        info = generations[c.id]
        p = planer[c.id]
        a_nr = tildelt[(c.id, "A")]
        b_nr = tildelt.get((c.id, "B"))
        maks_tett = 0.0
        for i in range(c.n_weeks):
            wk = c.start_week + i
            ba = p["A"][i] * p["w"][i]
            bb = p["B"][i] * p["w"][i]
            maks_tett = max(maks_tett, ba / V, bb / V)
            if wk >= n_uker:
                continue
            skott_kohort[a_nr - 1][wk], skott_status[a_nr - 1][wk], skott_bio[a_nr - 1][wk] = c.id, "Vekst", ba
            if b_nr is not None and p["split"] <= i <= p["release"]:
                skott_kohort[b_nr - 1][wk], skott_status[b_nr - 1][wk], skott_bio[b_nr - 1][wk] = c.id, "Vekst (splittet)", bb
        cw = int(info["cleaning_weeks"])
        for k in range(cw):
            for nr, fra in ((a_nr, c.start_week + c.n_weeks + k),
                            (b_nr, c.start_week + (p["release"] or 0) + 1 + k)):
                if nr is None or fra >= n_uker or (nr == b_nr and p["split"] is None):
                    continue
                if skott_status[nr - 1][fra] == "Ledig":
                    skott_kohort[nr - 1][fra], skott_status[nr - 1][fra] = f"({c.id})", "Rengjoring"
        info["skott_a"] = a_nr
        info["skott_b"] = b_nr
        info["split_week"] = (c.start_week + p["split"]) if p["split"] is not None else None
        info["skott_b_ledig_week"] = (c.start_week + p["release"] + 1) if p["split"] is not None else None
        info["max_density_kg_m3"] = maks_tett
        info["tetthet_over_tak"] = maks_tett > cap * (1 + 1e-6)
        info["mangler_skott"] = a_nr > n_skott or (b_nr is not None and b_nr > n_skott)

    sim_slutt = int(cfg.N_YEARS_TO_RUN) * 52
    konflikt_uker = int((belegg[:sim_slutt] > n_skott).sum())
    meta.update({
        "n_skott": n_skott,
        "m3_pool": V * n_skott,
        "skott_splitt": splitt,
        "skott_belegg": belegg.tolist(),
        "skott_maks_i_bruk": int(belegg[:sim_slutt].max()) if sim_slutt else 0,
        "skott_konflikt_uker": konflikt_uker,
        "skott_kohort": skott_kohort,
        "skott_biomasse_kg": skott_bio,
        "n_skott_totalt_inkl_mangler": n_tot,
    })

    # ---- weekly_df: skottrader + riktig tetthet per innsett og for anlegget ----
    labels = list(weekly_df.columns)
    ekstra_rader = []
    for nr in range(n_tot):
        navn = f"Skott {nr + 1}" + (" (MANGLER)" if nr >= n_skott else "")
        ekstra_rader.append({"felt": f"{navn} - kohort", **dict(zip(labels, skott_kohort[nr]))})
        ekstra_rader.append({"felt": f"{navn} - status", **dict(zip(labels, skott_status[nr]))})
        ekstra_rader.append({"felt": f"{navn} - biomasse (t)", **{l: round(b / 1000, 1) for l, b in zip(labels, skott_bio[nr])}})
        ekstra_rader.append({"felt": f"{navn} - tetthet (kg/m3)", **{l: round(b / V, 1) for l, b in zip(labels, skott_bio[nr])}})
    ekstra_rader.append({"felt": "Skott i bruk (inkl. vask)", **dict(zip(labels, belegg.tolist()))})
    df = weekly_df.copy()
    # "Tank t" = innsett t: tetthet = biomasse / volumet kohorten faktisk står i
    for t in range(1, int(cfg.N_TANKS) + 1):
        r_bio, r_dens, r_koh = f"Tank {t} - biomasse (t)", f"Tank {t} - tetthet (kg/m3)", f"Tank {t} - kohort"
        if r_dens not in df.index:
            continue
        for wk, lbl in enumerate(labels):
            gid = df.at[r_koh, lbl]
            if gid and gid in generations and generations[gid].get("skott_b") is not None:
                info = generations[gid]
                if info["split_week"] <= wk < info["skott_b_ledig_week"]:
                    df.at[r_dens, lbl] = round(float(df.at[r_bio, lbl]) * 1000 / (2 * V), 1)
    if "Anlegg totalt - biomasse (t)" in df.index:
        df.loc["Anlegg totalt - tetthet (kg/m3, snitt)"] = [
            round(float(v) * 1000 / (V * n_skott), 1) for v in df.loc["Anlegg totalt - biomasse (t)"]]
    df.index = df.index.rename(None)
    ny = pd.DataFrame(ekstra_rader).set_index("felt")
    df = pd.concat([df, ny])
    df.index.name = "felt"
    return df


# ----------------------------------------------------------------------
# Optimalisering av smoltantall per innsett
# ----------------------------------------------------------------------
def _per_fisk(c, info):
    N0 = float(info["stocked"])
    return {
        "sb": [x / N0 for x in c.weekly_standing_biomass_kg],
        "sc": [x / N0 for x in c.weekly_standing_count],
        "ac": [x / N0 for x in c.weekly_count_alive],
        "w": list(c.weekly_weight_kg),
        "hpf": sum(b["delivered_biomass_kg"] for b in info["batches"]) / N0,
        "start": c.start_week, "n": c.n_weeks, "cw": int(info["cleaning_weeks"]),
    }


def _tetthet_pf(pf, split):
    A, B, rel = _fordeling(pf["sb"], pf["sc"], pf["ac"], pf["w"], split)
    return max(max(a * x for a, x in zip(A, pf["w"])), max(b * x for b, x in zip(B, pf["w"]))), rel


def _kandidat_n(pf, cap_kg, splitt, margin, avrund):
    """Alle smoltantall som ligger akkurat på taket for en split i uke s."""
    ut = set()
    for s in [None] + (list(range(pf["n"] - 1, 0, -1)) if splitt else []):
        tett, _ = _tetthet_pf(pf, s)
        if tett > 0:
            N = math.floor(cap_kg / tett * margin / avrund) * avrund
            if N > 0:
                ut.add(N)
    return ut


def _evaluer(pfs, N, cap_kg, splitt, horisont):
    """Belegg (skott i bruk per uke) og levert biomasse for ÉN innsettplass
    med N fisk i alle generasjoner. None hvis tetthetstaket brytes."""
    arr = np.zeros(horisont, dtype=int)
    levert = 0.0
    for pf in pfs:
        s = next((i for i, b in enumerate(pf["sb"]) if N * b > cap_kg * (1 + 1e-9)), None) if splitt else None
        if s == 0:
            return None
        tett, rel = _tetthet_pf(pf, s)
        if tett * N > cap_kg * (1 + 1e-6):
            return None
        s0 = pf["start"]
        arr[s0: s0 + pf["n"] + pf["cw"]] += 1
        if s is not None:
            arr[s0 + s: s0 + rel + pf["cw"] + 1] += 1
        levert += N * pf["hpf"]
    return arr, levert


def optimaliser_smolt(cfg, generations, cohorts, margin=0.99, avrund=1000):
    """Returnerer dict med 'smolt' (liste per innsettplass), 'levert_t_per_ar'
    og 'maks_skott'. Bruker per-fisk-kurvene til ALLE kohortene i siste
    kjøring (biomasse er lineær i antall fisk), så sesongforskyvningen fra år
    til år er med. Grådig: øker smoltantallet der det gir mest levert
    biomasse per ekstra skott-uke, så lenge tetthet <= tak i hvert skott og
    aldri flere enn N_SKOTT skott er i bruk samtidig."""
    n_skott = int(getattr(cfg, "N_SKOTT", getattr(cfg, "N_TANKS", 1)))
    V = float(cfg.TANK_VOLUME_M3)
    cap_kg = float(cfg.MAX_DENSITY_KG_M3) * V
    splitt = bool(getattr(cfg, "SKOTT_SPLITT", True))
    n_plass = int(cfg.N_TANKS)
    sim_slutt = int(cfg.N_YEARS_TO_RUN) * 52

    pfs = {}
    for t in range(1, n_plass + 1):
        cs = [c for c in cohorts if generations[c.id]["tank"] == t and generations[c.id]["stocked"] > 0]
        if not cs:
            return None
        pfs[t] = [_per_fisk(c, generations[c.id]) for c in cs]
    horisont = max(pf["start"] + pf["n"] + pf["cw"] + 1 for lst in pfs.values() for pf in lst)

    kand = {}
    for t, lst in pfs.items():
        ns = set()
        for pf in lst:
            ns |= _kandidat_n(pf, cap_kg, splitt, margin, avrund)
        kand[t] = sorted(ns)

    valg, bel, lev = {}, {}, {}
    for t in pfs:
        # start: høyeste N som holder taket UTEN split (eller minste mulige)
        valg[t], bel[t], lev[t] = None, np.zeros(horisont, dtype=int), 0.0
        for k, N in enumerate(kand[t]):
            r = _evaluer(pfs[t], N, cap_kg, False, horisont)
            if r is None:
                break
            valg[t], (bel[t], lev[t]) = k, r
        if valg[t] is None:
            valg[t], bel[t], lev[t] = -1, np.zeros(horisont, dtype=int), 0.0
    cache = {}

    def ev(t, k):
        if (t, k) not in cache:
            cache[(t, k)] = _evaluer(pfs[t], kand[t][k], cap_kg, splitt, horisont) if k >= 0 else (
                np.zeros(horisont, dtype=int), 0.0)
        return cache[(t, k)]

    def fyll(valg, bel, lev, laast=()):
        """Grådig: øk smoltantallet der det gir mest levert per ekstra skott-uke."""
        valg, bel, lev = dict(valg), dict(bel), dict(lev)
        total = sum(bel.values())
        while True:
            beste = None
            for t in pfs:
                if t in laast:
                    continue
                for k in range(valg[t] + 1, len(kand[t])):
                    r = ev(t, k)
                    if r is None:
                        continue          # bryter taket for en av generasjonene - prøv neste
                    ny, ny_lev = r
                    test = total - bel[t] + ny
                    if test[:sim_slutt].max() > n_skott:
                        continue          # får ikke plass - et høyere antall kan splitte annerledes
                    if ny_lev <= lev[t]:
                        continue
                    score = (ny_lev - lev[t]) / max(1, int(ny.sum() - bel[t].sum()))
                    if beste is None or score > beste[0]:
                        beste = (score, t, k, ny, ny_lev)
                    break
            if beste is None:
                return valg, bel, lev
            _, t, k, ny, ny_lev = beste
            total = total - bel[t] + ny
            bel[t], valg[t], lev[t] = ny, k, ny_lev

    # Gulv per innsett: minst det antallet som fyller kohortens EGET skott til
    # taket uten split (ellers "lønner" det seg å sulte ut ett innsett og i
    # praksis kjøre færre innsett per år).
    gulv = dict(valg)
    valg, bel, lev = fyll(valg, bel, lev)
    # Lokalt søk: trekk én innsettplass ned noen steg og fyll opp de andre på
    # nytt - godta hvis samlet levert biomasse øker.
    forbedret = True
    while forbedret:
        forbedret = False
        for t in pfs:
            for tilbake in range(1, 30):
                k = valg[t] - tilbake
                if k < gulv[t]:
                    break
                r = ev(t, k)
                if r is None:
                    continue
                v2, b2, l2 = dict(valg), dict(bel), dict(lev)
                v2[t], (b2[t], l2[t]) = k, r
                v2, b2, l2 = fyll(v2, b2, l2, laast=(t,))
                v2, b2, l2 = fyll(v2, b2, l2)
                if sum(l2.values()) > sum(lev.values()) * (1 + 1e-6):
                    valg, bel, lev = v2, b2, l2
                    forbedret = True
                    break
            if forbedret:
                break
    total = sum(bel.values())

    smolt = [kand[t][valg[t]] if valg[t] >= 0 else 0 for t in range(1, n_plass + 1)]
    ar = sim_slutt / 52 if sim_slutt else 1
    return {
        "smolt": smolt,
        "levert_t_per_ar": sum(lev.values()) / 1000 / ar,
        "maks_skott": int(total[:sim_slutt].max()),
    }
