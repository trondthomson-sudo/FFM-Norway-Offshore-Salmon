"""
produksjonsstrategi.py - samlet oversikt over produksjonsstrategien (landanlegg -> Big Dipper -> slakt)
------------------------------------------------------------------------------
Brukes av visningen "Produksjonsstrategi" i ffm_big_dipper.py. Kjører de to
verifiserte schedulerne direkte med modellens STANDARDFORUTSETNINGER
(config_postsmolt.py + config_1tank.py, skyveskott) - samme tall som
visningene "Post-smolt landanlegg" og "SFaaS oppdrett" gir med
standardverdier - og trekker ut det som trengs for å forklare strategien
for noen som ser den for første gang:

  * hvor mange fisk som settes inn i landanlegget (30 g), og når
  * hvor lenge fisken står i hvert trinn (yngel / smolt / post-smolt), vekt
    og antall ved hver overgang, og hvor mange kar som brukes
  * vekt og antall når fisken går i sjø (Big Dipper)
  * vekst i Big Dipper, slakteperiode, slaktevekt og levert volum
    per kohort

Tre byggesteiner: beregn_strategi() (tall), tegn_flytdiagram() og
tegn_tidslinje() (figurer).
"""

from __future__ import annotations
from datetime import date, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch, Patch
import pandas as pd

import config_1tank as bd_config
import config_postsmolt as ps_config
from growth_tables import GrowthTables
from scheduler_multitank import build_multitank_schedule, _cfg_kopi
from scheduler_postsmolt import build_postsmolt_schedule
from scheduler_1tank import monday_of_week

TRINN_FARGE = {"yngel": "#8fb8de", "smolt": "#4f8fc0", "postsmolt": "#2f5d8a", "fase2": "#1f3f60"}
SJO_FARGE = "#3a8a5c"
SLAKT_FARGE = "#c0522b"
MND = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"]


def _sp(x, dec=0) -> str:
    """Tall med mellomrom som tusenskille og komma som desimaltegn."""
    s = f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")
    return s


def _dato(d: date) -> str:
    return f"{d.day}. {MND[d.month - 1]} {d.year}"


def _forste_mandag(year, month):
    d = date(year, month, 1)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def beregn_strategi(n_abd: int = 1, design_navn: str | None = None, leveranse_ar: int | None = None):
    """Returnerer dict med 'land' (liste per kohort), 'sjo' (liste per kohort),
    'trinn' (aktive trinn med kar) og nøkkeltall. Én rad per kohort i
    LEVERANSEÅRET (standard: første leveranseår)."""
    gt = GrowthTables(fcr_csv="data/fcr_table.csv", sgr_csv="data/sgr_table.csv")

    # ---------------- Landanlegget ----------------
    pc = _cfg_kopi(ps_config)
    designs = getattr(ps_config, "ANLEGG_DESIGN", {})
    design_navn = design_navn or getattr(ps_config, "DEFAULT_ANLEGG_DESIGN", None)
    design = designs.get(design_navn) if design_navn else None
    trinn = [dict(t) for t in ps_config.TRINN]
    if design:
        for t in trinn:
            if t["id"] in design["trinn"]:
                t["antall_kar"], t["kar_volum_m3"], t["tetthetstak_kg_m3"] = design["trinn"][t["id"]]
        n_abd = int(design.get("n_abd", n_abd))
    if n_abd >= 2:
        # samme regel som i appen: to ABD-er uten fase 2-hallen går bare i I.B-designet
        pass
    pc.TRINN = trinn
    pc.N_ABD = int(n_abd)
    pc.N_YEARS_TO_RUN = 2
    pc.RESERVERTE_KAR = dict(design.get("reservert", {})) if design else {}
    _, pgens, pcoh, pmeta = build_postsmolt_schedule(pc, gt)
    lev_ar = leveranse_ar or int(pc.LEVERANSE_START_AR)
    p_by_id = {c.id: c for c in pcoh}
    anker_p = monday_of_week(0, pc.START_ISO_YEAR, pc.START_ISO_WEEK)

    land = []
    for gid, info in sorted(pgens.items(), key=lambda kv: kv[1]["leveringsdato"]):
        if info["leveringsdato"].year != lev_ar:
            continue
        c = p_by_id[gid]
        innsett = anker_p + timedelta(weeks=info["start_week"])
        faser = []
        i = 0
        tp = info["trinn_per_uke"]
        while i < len(tp):
            j = i
            while j < len(tp) and tp[j] == tp[i]:
                j += 1
            tdef = next(t for t in pmeta["trinn"] if t["id"] == tp[i])
            ant_inn = info["stocked"] if i == 0 else c.weekly_count_alive[i - 1]
            vekt_inn = info["start_weight_kg"] if i == 0 else c.weekly_weight_kg[i - 1]
            faser.append({
                "trinn": tp[i], "navn": tdef["navn"], "uker": j - i,
                "fra_dato": innsett + timedelta(weeks=i), "til_dato": innsett + timedelta(weeks=j),
                "vekt_inn_g": vekt_inn * 1000, "vekt_ut_g": c.weekly_weight_kg[j - 1] * 1000,
                "antall_inn": ant_inn, "antall_ut": c.weekly_count_alive[j - 1],
                "maks_kar": max(info["kar_behov_per_uke"][i:j]),
                "maks_biomasse_t": max(c.weekly_standing_biomass_kg[i:j]) / 1000,
            })
            i = j
        land.append({
            "id": gid, "abd": info["abd"], "innsett_dato": innsett, "levering_dato": info["leveringsdato"],
            "antall_inn": info["stocked"], "vekt_inn_g": info["start_weight_kg"] * 1000,
            "uker": info["growth_weeks"], "antall_ut": info["survivors_at_delivery"],
            "vekt_ut_g": info["delivery_weight_kg"] * 1000, "biomasse_ut_t": info["delivered_biomass_kg"] / 1000,
            "faser": faser,
        })

    # ---------------- Big Dipper (skyveskott, standard) ----------------
    bc = _cfg_kopi(bd_config)
    d = bd_config.SKYVESKOTT_DEFAULTS
    n_t = int(d["n_tanks"])
    bc.N_TANKS, bc.TANK_VOLUME_M3 = n_t, float(d["tank_volume_m3"])
    bc.BATCH_SMOLT_COUNTS = list(d["smolt"])
    bc.BATCH_START_WEIGHT_KG = [0.75] * n_t
    bc.BATCH_GROWTH_WEEKS = [50] * n_t
    bc.BATCH_CLEANING_WEEKS = [1] * n_t
    bc.BATCH_SALES_WINDOW_WEEKS = [8] * n_t
    bc.SKOTT_FASTE = False
    bc.N_YEARS_TO_RUN = 2
    y0 = lev_ar
    d0 = _forste_mandag(y0, 1)
    iso = d0.isocalendar()
    bc.START_ISO_YEAR, bc.START_ISO_WEEK = int(iso[0]), int(iso[1])
    steg = 12 // n_t if 12 % n_t == 0 else None
    if steg:
        bc.TANK_START_WEEK_OFFSETS = [(_forste_mandag(y0, 1 + t * steg) - d0).days // 7 for t in range(n_t)]
    else:
        bc.TANK_START_WEEK_OFFSETS = [round(t * 52 / n_t) for t in range(n_t)]
    _, bgens, bcoh, bmeta = build_multitank_schedule(bc, gt)
    b_by_id = {c.id: c for c in bcoh}
    anker_b = monday_of_week(0, bc.START_ISO_YEAR, bc.START_ISO_WEEK)
    hog = float(getattr(bd_config, "HOG_FAKTOR", 0.825))

    sjo = []
    for gid, info in bgens.items():
        if not gid.startswith("G1-"):
            continue
        c = b_by_id[gid]
        antall_sl = sum(b["delivered_count"] for b in info["batches"])
        kg_sl = sum(b["delivered_biomass_kg"] for b in info["batches"])
        uker_sl = [b["delivery_week"] for b in info["batches"]]
        sjo.append({
            "id": gid, "innsett_dato": anker_b + timedelta(weeks=info["start_week"]),
            "antall_inn": info["stocked"], "vekt_inn_g": info["start_weight_kg"] * 1000,
            "uker": info["growth_weeks"], "salgsvindu_uker": info["sales_window_weeks"],
            "slakt_fra": anker_b + timedelta(weeks=min(uker_sl)),
            "slakt_til": anker_b + timedelta(weeks=max(uker_sl) + 1) - timedelta(days=1),
            "antall_slaktet": antall_sl, "snittvekt_slakt_g": kg_sl / antall_sl * 1000 if antall_sl else 0.0,
            "siste_vekt_g": info["delivery_weight_kg"] * 1000,
            "levert_t_wfe": kg_sl / 1000, "levert_t_hog": kg_sl * hog / 1000,
            "maks_biomasse_t": max(c.weekly_standing_biomass_kg) / 1000,
            "maks_m3": info.get("max_m3_behov", 0.0),
            "fcr": info["overall_fcr"],
        })
    sjo.sort(key=lambda r: r["innsett_dato"])

    # Slaktet i KALENDERÅRET etter leveranseåret (første fulle slakteår) - samme
    # måltall som massebalansen i SFaaS-visningen. År = ukens torsdag (ISO).
    kal_ar = lev_ar + 1
    hog_kal = 0.0
    for info in bgens.values():
        for b in info["batches"]:
            dd = anker_b + timedelta(weeks=b["delivery_week"], days=3)
            if dd.isocalendar()[0] == kal_ar:
                hog_kal += b["delivered_biomass_kg"] * hog / 1000
    syklus_uker = (sjo[0]["uker"] + int(bc.BATCH_CLEANING_WEEKS[0])) if sjo else 51

    return {
        "land": land, "sjo": sjo, "trinn": pmeta["trinn"], "vekstuker_land": pmeta["vekstuker"],
        "n_abd": pmeta["n_abd"], "design": design_navn, "leveranse_ar": lev_ar,
        "hog_kalenderar_t": hog_kal, "kalenderar": kal_ar, "syklus_uker": syklus_uker,
        "bd_tankvolum": float(d["tank_volume_m3"]), "bd_n": n_t, "bd_tetthet": float(bd_config.MAX_DENSITY_KG_M3),
        "hog": hog, "dodelighet_pct_ar": float(bd_config.ANNUAL_MORTALITY_PCT),
        "ras_temp": ps_config.MONTHLY_TEMPERATURES_C, "sjo_temp": bd_config.MONTHLY_TEMPERATURES_C,
    }


# ----------------------------------------------------------------------
# Tabeller
# ----------------------------------------------------------------------
def tabell_land(res) -> pd.DataFrame:
    rader = []
    for k in res["land"]:
        r = {"Kohort": k["id"], "Inn i landanlegget": _dato(k["innsett_dato"]),
             "Antall inn (stk)": _sp(k["antall_inn"]), "Vekt inn (g)": _sp(k["vekt_inn_g"])}
        for f in k["faser"]:
            kort = f["navn"].split(" (")[0]
            r[f"{kort}: uker"] = f["uker"]
            r[f"{kort}: vekt ut (g)"] = _sp(f["vekt_ut_g"])
            r[f"{kort}: maks kar"] = f["maks_kar"]
        r.update({"Uker på land": k["uker"], "Ut i sjø (dato)": _dato(k["levering_dato"]),
                  "Antall ut (stk)": _sp(k["antall_ut"]), "Vekt ut (g)": _sp(k["vekt_ut_g"]),
                  "Biomasse ut (t)": _sp(k["biomasse_ut_t"])})
        rader.append(r)
    return pd.DataFrame(rader)


def tabell_sjo(res) -> pd.DataFrame:
    rader = []
    for k in res["sjo"]:
        rader.append({
            "Kohort": k["id"], "Inn i Big Dipper": _dato(k["innsett_dato"]),
            "Antall inn (stk)": _sp(k["antall_inn"]), "Vekt inn (g)": _sp(k["vekt_inn_g"]),
            "Uker i sjø": k["uker"], "Slakteperiode": f"{_dato(k['slakt_fra'])} – {_dato(k['slakt_til'])}",
            "Antall slaktet (stk)": _sp(k["antall_slaktet"]), "Snittvekt slakt (g WFE)": _sp(k["snittvekt_slakt_g"]),
            "Levert (t WFE)": _sp(k["levert_t_wfe"]), "Levert (t HOG)": _sp(k["levert_t_hog"]),
            "Maks stående biomasse (t)": _sp(k["maks_biomasse_t"]), "FCR": _sp(k["fcr"], 2),
        })
    return pd.DataFrame(rader)


# ----------------------------------------------------------------------
# Flytdiagram
# ----------------------------------------------------------------------
def _boks(ax, x, y, w, h, farge, tittel, linjer, tekstfarge="white"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                fc=farge, ec="none"))
    ax.text(x + w / 2, y + h - 0.25, tittel, ha="center", va="top", fontsize=16, fontweight="bold", color=tekstfarge)
    ax.text(x + w / 2, y + h - 0.88, "\n".join(linjer), ha="center", va="top", fontsize=15,
            color=tekstfarge, linespacing=1.5)


def _pil(ax, x0, x1, y, over, under=""):
    ax.annotate("", xy=(x1, y), xytext=(x0, y),
                arrowprops=dict(arrowstyle="-|>", lw=2.2, color="#444", mutation_scale=18))
    ax.text((x0 + x1) / 2, y + 0.12, over, ha="center", va="bottom", fontsize=11.5, color="#222", fontweight="bold")
    if under:
        ax.text((x0 + x1) / 2, y - 0.12, under, ha="center", va="top", fontsize=10.5, color="#555")


def kommentar_kalenderar(res, linjeskift: bool = True):
    """(tittel, tekst) som forklarer hvorfor én runde med kohorter gir litt
    mindre enn ett kalenderår (51 ukers syklus mot 52 uker i året)."""
    sjo = res["sjo"]
    runde = sum(k["levert_t_hog"] for k in sjo)
    syk = int(res["syklus_uker"])
    nl = "\n" if linjeskift else " "
    tekst = (f"{_sp(runde)} t HOG = én runde med {len(sjo)} kohorter. Hver kohort står {syk - 1} uker i sjø + 1 vaskeuke = "
             f"{syk} uker,{nl}mens året har 52. Innsettet kommer derfor 1 uke tidligere hvert år, og det slaktes i snitt "
             f"52/{syk} ≈ {_sp(52 / syk * len(sjo), 1)} kohorter{nl}per kalenderår: {_sp(runde)} × 52/{syk} ≈ {_sp(runde * 52 / syk)} t. "
             f"Modellen ({res['kalenderar']}): {_sp(res['hog_kalenderar_t'])} t HOG – samme tall som massebalansen i SFaaS.")
    return "Hvorfor ikke samme tall som i SFaaS?", tekst


def tegn_flytdiagram(res, med_kommentar: bool = False):
    """Én kohort fulgt fra innsett på land til slakt - snitt over årets kohorter."""
    land, sjo = res["land"], res["sjo"]
    n_land = len(land)
    snitt = lambda xs: sum(xs) / len(xs) if xs else 0.0
    # snitt per trinn over kohortene
    trinn_ids = [f["trinn"] for f in land[0]["faser"]] if land else []
    fase_snitt = []
    for i, tid in enumerate(trinn_ids):
        fs = [k["faser"][i] for k in land if i < len(k["faser"])]
        tdef = next(t for t in res["trinn"] if t["id"] == tid)
        fase_snitt.append({
            "trinn": tid, "navn": tdef["navn"].split(" (")[0], "uker": snitt([f["uker"] for f in fs]),
            "vekt_inn": snitt([f["vekt_inn_g"] for f in fs]), "vekt_ut": snitt([f["vekt_ut_g"] for f in fs]),
            "antall_inn": snitt([f["antall_inn"] for f in fs]), "antall_ut": snitt([f["antall_ut"] for f in fs]),
            "kar": max(f["maks_kar"] for f in fs), "kar_tot": int(tdef["antall_kar"]),
            "karvol": float(tdef["kar_volum_m3"]), "tak": float(tdef["tetthetstak_kg_m3"]),
        })

    n_bokser = len(fase_snitt) + 3
    W, H = 3.7, 3.6
    gap = 1.55
    fig_w = n_bokser * W + (n_bokser - 1) * gap + 0.6
    fig, ax = plt.subplots(figsize=(fig_w * 0.85, 7.6))
    ax.set_xlim(0, fig_w)
    ax.set_ylim(-1.9, H + 1.55)
    ax.axis("off")
    y = 0.0
    x = 0.3

    # 1) Inn
    ant_inn = snitt([k["antall_inn"] for k in land])
    _boks(ax, x, y, W, H, "#e9eef4", "Innsett på land",
          [f"{_sp(ant_inn)} yngel", f"à {_sp(land[0]['vekt_inn_g'] if land else 30)} g",
           "fra eget klekkeri /", "startfôring", "",
           f"{n_land} innsett per år"
           + (f"\n({res['n_abd']} ABD)" if res["n_abd"] > 1 else "")], tekstfarge="#1b2a3a")
    x_prev = x + W
    x += W + gap
    # 2) Trinn på land
    for f in fase_snitt:
        _pil(ax, x_prev, x, H / 2, f"{_sp(f['antall_inn'])} stk", f"{_sp(f['vekt_inn'])} g")
        _boks(ax, x, y, W, H, TRINN_FARGE.get(f["trinn"], "#2f5d8a"), f["navn"],
              [f"{_sp(f['vekt_inn'])} → {_sp(f['vekt_ut'])} g",
               f"{_sp(f['uker'], 0)} uker",
               f"inntil {f['kar']} av {f['kar_tot']} kar",
               f"à {_sp(f['karvol'])} m³",
               f"maks {_sp(f['tak'])} kg/m³"])
        x_prev = x + W
        x += W + gap
    # 3) Big Dipper
    k_ut = snitt([k["antall_ut"] for k in land])
    v_ut = snitt([k["vekt_ut_g"] for k in land])
    _pil(ax, x_prev, x, H / 2, f"{_sp(k_ut)} stk", f"{_sp(v_ut)} g\nbrønnbåt")
    s_inn = snitt([k["antall_inn"] for k in sjo])
    s_uker = snitt([k["uker"] for k in sjo])
    s_bio = max(k["maks_biomasse_t"] for k in sjo) if sjo else 0
    _boks(ax, x, y, W, H, SJO_FARGE, "Big Dipper (sjø)",
          [f"{_sp(snitt([k['vekt_inn_g'] for k in sjo]))} → {_sp(snitt([k['siste_vekt_g'] for k in sjo]))} g",
           f"{_sp(s_uker)} uker",
           f"{res['bd_n']} kohorter samtidig",
           f"skyveskott, {_sp(round(res['bd_n'] * res['bd_tankvolum'], -3))} m³",
           f"maks {_sp(res['bd_tetthet'])} kg/m³"])
    x_prev = x + W
    x += W + gap
    # 4) Slakt
    a_sl = snitt([k["antall_slaktet"] for k in sjo])
    _pil(ax, x_prev, x, H / 2, f"{_sp(a_sl)} stk", f"over {_sp(snitt([k['salgsvindu_uker'] for k in sjo]))} uker")
    _boks(ax, x, y, W, H, SLAKT_FARGE, "Slakt og salg",
          [f"snittvekt {_sp(snitt([k['snittvekt_slakt_g'] for k in sjo]))} g",
           f"{_sp(snitt([k['levert_t_wfe'] for k in sjo]))} t WFE per kohort",
           f"= {_sp(snitt([k['levert_t_hog'] for k in sjo]))} t HOG",
           "",
           f"{_sp(sum(k['levert_t_hog'] for k in sjo))} t HOG per runde",
           f"(≈ {_sp(res['hog_kalenderar_t'])} t per kalenderår)"])
    x_bd = x - (W + gap)          # venstre kant av Big Dipper-boksen (kommentarboksen legges over den og slakt)

    # Tidslinje-bånd under: land-tid og sjø-tid
    land_uker = res["vekstuker_land"]
    tot = land_uker + s_uker
    x0, x1 = 0.3, fig_w - 0.3
    xm = x0 + (x1 - x0) * land_uker / tot
    ax.add_patch(FancyBboxPatch((x0, -1.35), xm - x0 - 0.05, 0.42, boxstyle="round,pad=0.01", fc="#2f5d8a", ec="none"))
    ax.add_patch(FancyBboxPatch((xm + 0.05, -1.35), x1 - xm - 0.05, 0.42, boxstyle="round,pad=0.01", fc=SJO_FARGE, ec="none"))
    ax.text((x0 + xm) / 2, -1.14, f"Landanlegg: {land_uker} uker (~{_sp(land_uker / 4.345, 0)} mnd)",
            ha="center", va="center", color="white", fontsize=10, fontweight="bold")
    ax.text((xm + x1) / 2, -1.14, f"Big Dipper: {_sp(s_uker)} uker (~{_sp(s_uker / 4.345, 0)} mnd) inkl. {_sp(snitt([k['salgsvindu_uker'] for k in sjo]))} ukers slakting",
            ha="center", va="center", color="white", fontsize=10, fontweight="bold")
    ax.text(x0, -1.62, f"Fra 30 g til slakt: ca. {_sp(tot)} uker ({_sp(tot / 52, 1)} år). "
            "Tall = snitt over årets kohorter; antall er fisk som går videre etter dødelighet "
            f"({_sp(res['dodelighet_pct_ar'], 1)} %/år). Big Dipper-modellen regner med 750 g inn; landanlegget "
            f"leverer {_sp(v_ut)} g.", fontsize=9, color="#444", va="top")
    # ---- Kommentarboks (kun i nedlastet PNG - i appen vises den som egen boks over figuren) ----
    if med_kommentar:
        tittel, tekst = kommentar_kalenderar(res)
        kx0, kx1 = x_bd - (W + gap), fig_w - 0.3
        ax.add_patch(FancyBboxPatch((kx0, H + 0.12), kx1 - kx0, 1.28, boxstyle="round,pad=0.04,rounding_size=0.12",
                                    fc="#fff6d6", ec="#d4a017", lw=1.2))
        ax.text(kx0 + 0.15, H + 1.30, tittel, fontsize=10.5, fontweight="bold", va="top", color="#5a4300")
        ax.text(kx0 + 0.15, H + 0.98, tekst, fontsize=9.2, va="top", color="#3d3000", linespacing=1.4)
    ax.text(x0, H + 1.35, f"Produksjonsstrategien – én kohort fra innsett på land til slakt ({res['leveranse_ar']})",
            fontsize=15, fontweight="bold", va="top", color="#1b2a3a")
    ax.text(x0, H + 0.8, f"{n_land} kohorter per år: settes inn på land hver annen måned, går i sjø første mandag i "
            "jan/mar/mai/jul/sep/nov og slaktes ut ca. ett år senere.", fontsize=10.5, va="top", color="#444")
    fig.tight_layout()
    return fig


def tegn_tidslinje(res):
    """Gantt: årets kohorter - trinn på land, vekst i sjø, slakteperiode."""
    land = {k["levering_dato"]: k for k in res["land"]}
    sjo = res["sjo"]
    n = len(sjo)
    fig, ax = plt.subplots(figsize=(20, 1.0 + 0.72 * n))
    for i, s in enumerate(sjo):
        yy = n - 1 - i
        # land-delen: finn kohorten som leveres i uka Big Dipper-kohorten settes inn
        lk = min(res["land"], key=lambda k: abs((k["levering_dato"] - s["innsett_dato"]).days)) if res["land"] else None
        if lk:
            for f in lk["faser"]:
                ax.barh(yy, (f["til_dato"] - f["fra_dato"]).days, left=mdates.date2num(f["fra_dato"]), height=0.55,
                        color=TRINN_FARGE.get(f["trinn"], "#2f5d8a"), edgecolor="white", linewidth=0.8)
            ax.text(mdates.date2num(lk["innsett_dato"]) - 4, yy,
                    f"{lk['id']}  {_sp(lk['antall_inn'])} stk à {_sp(lk['vekt_inn_g'])} g", ha="right", va="center", fontsize=9)
        vekst_til = s["slakt_fra"]
        ax.barh(yy, (vekst_til - s["innsett_dato"]).days, left=mdates.date2num(s["innsett_dato"]), height=0.55,
                color=SJO_FARGE, edgecolor="white", linewidth=0.8)
        ax.barh(yy, (s["slakt_til"] - s["slakt_fra"]).days + 1, left=mdates.date2num(s["slakt_fra"]), height=0.55,
                color=SLAKT_FARGE, edgecolor="white", linewidth=0.8)
        ax.text(mdates.date2num(s["innsett_dato"]) + 3, yy,
                f"{s['id']}: {_sp(s['antall_inn'])} stk à {_sp(s['vekt_inn_g'])} g i sjø", ha="left", va="center",
                fontsize=9, color="white", fontweight="bold")
        ax.text(mdates.date2num(s["slakt_til"]) + 4, yy,
                f"{_sp(s['antall_slaktet'])} stk · {_sp(s['snittvekt_slakt_g'])} g · {_sp(s['levert_t_hog'])} t HOG",
                ha="left", va="center", fontsize=9)
    ax.set_yticks([])
    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    ax.grid(axis="x", alpha=0.3)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    xs = [mdates.date2num(k["innsett_dato"]) for k in res["land"]] + [mdates.date2num(s["slakt_til"]) for s in sjo]
    if xs:
        ax.set_xlim(min(xs) - 120, max(xs) + 150)
    handles = [Patch(fc=TRINN_FARGE[t["id"]], label=t["navn"]) for t in res["trinn"] if t["id"] in TRINN_FARGE]
    handles += [Patch(fc=SJO_FARGE, label="Vekst i Big Dipper"), Patch(fc=SLAKT_FARGE, label="Slakting (batchvis)")]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=len(handles), frameon=False, fontsize=10)
    ax.set_title(f"Tidslinje per kohort – fisk som går i sjø i {res['leveranse_ar']}", loc="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    return fig
