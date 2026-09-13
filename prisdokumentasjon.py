"""
prisdokumentasjon.py
--------------------
Dokumentasjon av lakseprisforutsetningen - tegnes øverst i visningen
"Oppsummering (1 ABD)". INGEN beregning: figuren viser bare (1) historisk
årssnitt, (2) den eksterne estimatbanen (trend + syklisk) og (3) modellens
egen prisbane (startpris x årlig eskalering, forskjøvet til faktisk
oppstartsår), slik at prisdiskusjonen kan tas med alle tre på samme akse.

Tallene i LAKSEPRIS_* under er hentet EKSAKT fra arbeidsboken
"Laksepris_narrativ_og_scenarier_20260913.xlsx" (ark "Price narrative" og
"Assumptions"): NOS spot-pris ukentlig 2005-2025 (årssnitt), lineær trend
(NOK 3,2/kg per år, kalibrert på årssnitt 2005-2025) og syklisk
forlengelse (syklus ca. 6,1 år, illustrativ amplitude +/- NOK 12,5/kg
rundt trenden). Oppdater ved ny versjon av arbeidsboken.

Kalles fra ffm_big_dipper.py (_render_oppsummering), HELT NEDERST i
visningen, med startpris 100 kr/kg og 2,0 %/år hardkodet - ingen
justering i appen; endres kun i kallet hvis modellens prisforutsetning
endres.
"""

from __future__ import annotations
import pandas as pd
import matplotlib.pyplot as plt

# Kilde og dato for estimatgrafen - fylles inn (vises i forutsetningsboksen).
LAKSEPRIS_KILDE = ("Trident-notat 'Laksepris_narrativ_og_scenarier' (13.09.2026): NOS spot-pris 2005-2025, lineær trend "
                   "NOK 3,2/kg per år, syklisk forlengelse (6,1 års syklus, +/- NOK 12,5/kg) - beskrivende syklusmodell, ikke punktprognose")

# Historisk årssnitt, NOK/kg (avlest)
LAKSEPRIS_HISTORISK = {
    2005: 25.28, 2006: 32.08, 2007: 24.98, 2008: 25.76, 2009: 30.62, 2010: 37.45,
    2011: 31.27, 2012: 26.2, 2013: 39.07, 2014: 39.7, 2015: 40.49, 2016: 61.93,
    2017: 59.18, 2018: 59.22, 2019: 57.21, 2020: 53.7, 2021: 57.26, 2022: 81.95,
    2023: 91.61, 2024: 90.57, 2025: 76.73,
}

# Langsiktig nominell trend, NOK/kg (+3,2 kr/år): 97,3 i 2030, krysser
# 100 i 2031, 119,7 i 2037, 129,2 i 2040.
LAKSEPRIS_TREND = {
    2005: 17.43, 2006: 20.62, 2007: 23.82, 2008: 27.01, 2009: 30.2, 2010: 33.4,
    2011: 36.59, 2012: 39.79, 2013: 42.98, 2014: 46.18, 2015: 49.37, 2016: 52.57,
    2017: 55.76, 2018: 58.96, 2019: 62.15, 2020: 65.34, 2021: 68.54, 2022: 71.73,
    2023: 74.93, 2024: 78.12, 2025: 81.32, 2026: 84.51, 2027: 87.71, 2028: 90.9,
    2029: 94.1, 2030: 97.29, 2031: 100.49, 2032: 103.68, 2033: 106.87, 2034: 110.07,
    2035: 113.26, 2036: 116.46, 2037: 119.65, 2038: 122.85, 2039: 126.04, 2040: 129.24,
}

# Syklisk estimat, NOK/kg: 104,8 i 2030, bunn 91,3 i 2032, topp 125,5 i
# 2035, 116,2 i 2037, 132,1 i 2040.
LAKSEPRIS_SYKLISK = {
    2025: 76.73, 2026: 72.03, 2027: 80.73, 2028: 96.2, 2029: 106.53, 2030: 104.79,
    2031: 95.77, 2032: 91.33, 2033: 98.87, 2034: 114.18, 2035: 125.5, 2036: 124.95,
    2037: 116.16, 2038: 110.76, 2039: 117.09, 2040: 132.11,
}

# Likevektsscenarier fra samme arbeidsbok (ark "Assumptions"): normalisert
# startpris 86,3 kr/kg (snitt 2023-2025), årlig nominell prisvekst =
# (1+inflasjon) x (1+(etterspørselsvekst - tilbudsvekst)/|priselastisitet|) - 1.
# Konservativ 2,0 %/år, Basis 6,1 %/år, Stramt tilbud 8,9 %/år. Kun til
# tabellen - illustrativ likevektsanalyse, ikke punktprognose.
LAKSEPRIS_SCENARIER = {
    "Konservativ": {
    2025: 86.3, 2026: 88.03, 2027: 89.79, 2028: 91.59, 2029: 93.42, 2030: 95.29,
    2031: 97.19, 2032: 99.13, 2033: 101.12, 2034: 103.14, 2035: 105.2,
},
    "Basis": {
    2025: 86.3, 2026: 91.55, 2027: 97.12, 2028: 103.02, 2029: 109.28, 2030: 115.93,
    2031: 122.98, 2032: 130.45, 2033: 138.39, 2034: 146.8, 2035: 155.73,
},
    "Stramt tilbud": {
    2025: 86.3, 2026: 93.99, 2027: 102.36, 2028: 111.48, 2029: 121.4, 2030: 132.22,
    2031: 143.99, 2032: 156.82, 2033: 170.78, 2034: 185.99, 2035: 202.56,
},
}


def modellens_prisbane(startpris_kr_kg: float, eskalering_pct: float,
                       faktisk_startaar: int, n_aar: int) -> dict[int, float]:
    """Startpris i år 1 (= faktisk_startaar), deretter fast årlig eskalering.
    Samme prinsipp som 'inntekt'-linjen i eskaleringstabellen (fast fra
    1. januar, basert på leveringsår)."""
    return {faktisk_startaar + i: startpris_kr_kg * (1.0 + eskalering_pct / 100.0) ** i for i in range(n_aar)}


def sammenligningstabell(startpris_kr_kg: float, eskalering_pct: float,
                         modell_startaar: int, faktisk_startaar: int, n_aar: int) -> pd.DataFrame:
    bane = modellens_prisbane(startpris_kr_kg, eskalering_pct, faktisk_startaar, n_aar)
    rader = []
    for i, (aar, pris) in enumerate(bane.items()):
        trend = LAKSEPRIS_TREND.get(aar)
        syk = LAKSEPRIS_SYKLISK.get(aar)
        rader.append({
            "Modellår": modell_startaar + i,
            "Faktisk år": aar,
            "Modell (kr/kg)": round(pris, 1),
            "Trend (kr/kg)": trend,
            "Syklisk (kr/kg)": syk,
            "Modell vs trend": f"{(pris / trend - 1) * 100:+.1f} %" if trend else "-",
            "Modell vs syklisk": f"{(pris / syk - 1) * 100:+.1f} %" if syk else "-",
            **{f"Scenario {navn.lower()} (kr/kg)": bane_sc.get(aar) for navn, bane_sc in LAKSEPRIS_SCENARIER.items()},
        })
    return pd.DataFrame(rader)


def tegn_prisfigur(startpris_kr_kg: float, eskalering_pct: float,
                   faktisk_startaar: int, n_aar: int):
    bane = modellens_prisbane(startpris_kr_kg, eskalering_pct, faktisk_startaar, n_aar)
    fig, ax = plt.subplots(figsize=(11, 4.2))
    h = LAKSEPRIS_HISTORISK
    ax.plot(list(h), list(h.values()), color="#2f6db3", lw=2.2, label="Historisk årssnitt")
    s = LAKSEPRIS_SYKLISK
    ax.plot(list(s), list(s.values()), color="#5aa02c", lw=2.0, ls=":", label="Syklisk estimat")
    t = LAKSEPRIS_TREND
    ax.plot(list(t), list(t.values()), color="#9a9a9a", lw=1.4, ls="--", label="Langsiktig trend")
    ax.plot(list(bane), list(bane.values()), color="#c0522b", lw=2.4, marker="o", ms=3.5,
            label=f"Modellen: {startpris_kr_kg:.0f} kr/kg fra {faktisk_startaar}, +{eskalering_pct:.1f} %/år")
    ax.axhline(100, color="#555", lw=1.0, ls="--", alpha=0.6)
    ax.text(2005.2, 101.5, "NOK 100", fontsize=8, color="#555")
    for aar in (faktisk_startaar, faktisk_startaar + n_aar // 2, min(faktisk_startaar + n_aar - 1, 2040)):
        if aar in bane:
            ax.annotate(f"{bane[aar]:.0f}", (aar, bane[aar]), textcoords="offset points", xytext=(0, -13),
                        ha="center", fontsize=8, color="#c0522b")
            if aar in t:
                ax.annotate(f"{t[aar]:.0f}", (aar, t[aar]), textcoords="offset points", xytext=(0, 6),
                            ha="center", fontsize=8, color="#777")
    ax.set_ylabel("NOK per kg (HOG, eksportpris)")
    ax.set_xlim(2004.5, 2040.5)
    ax.set_ylim(0, 140)
    ax.set_xticks(range(2005, 2041, 5))
    ax.grid(axis="y", ls="--", alpha=0.4)
    ax.legend(loc="upper left", fontsize=8.5, frameon=False, ncol=2)
    ax.set_title("Laksepris: historisk, eksternt estimat og modellens forutsetning", fontsize=11)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout()
    return fig


def tegn_prisdokumentasjon(st, startpris_kr_kg: float, eskalering_pct: float,
                           modell_startaar: int, faktisk_startaar: int, n_aar: int) -> None:
    """Streamlit-seksjon: figur + forutsetningsboks + tabell (i expander)."""
    st.subheader("Prisforutsetning - laks (kr/kg HOG)")
    fig = tegn_prisfigur(startpris_kr_kg, eskalering_pct, faktisk_startaar, n_aar)
    st.pyplot(fig, use_container_width=True)
    plt.close(fig)

    tab = sammenligningstabell(startpris_kr_kg, eskalering_pct, modell_startaar, faktisk_startaar, n_aar)
    _med_trend = tab[tab["Trend (kr/kg)"].notna()]
    _sist = _med_trend.iloc[-1]                       # siste år med referansetall (2040)
    _midt = _med_trend.iloc[len(_med_trend) // 2]
    _fulle = tab.iloc[1:3]                            # de to første FULLE driftsårene (år 1 er oppstart, første salg sent i året)
    _tidlig = ""
    if _fulle["Syklisk (kr/kg)"].notna().all():
        _tidlig = (f" I de to første fulle driftsårene ({int(_fulle.iloc[0]['Faktisk år'])}-{int(_fulle.iloc[-1]['Faktisk år'])}) ligger modellen "
                   f"{(_fulle['Modell (kr/kg)'] / _fulle['Syklisk (kr/kg)'] - 1).mean() * 100:+.0f} % over det sykliske estimatet, som har "
                   f"bunn i {int(tab.loc[tab['Syklisk (kr/kg)'].idxmin(), 'Faktisk år'])} - dette er årene som veier tyngst i nåverdi og lånedekning.")
    st.info(
        f"**Prisbasis:** NOK {startpris_kr_kg:.0f}/kg HOG i driftsår 1 (modellår {modell_startaar} = faktisk {faktisk_startaar}), "
        f"{eskalering_pct:.1f} % nominell eskalering per år. Lønn eskaleres 3 %/år, øvrige kostnader 2 %/år (se 'Årlig eskalering'). "
        f"Den lineære trenden (NOK 3,2/kg per år, kalibrert på NOS spot 2005-2025) er {LAKSEPRIS_TREND[faktisk_startaar]:.1f} kr/kg i {faktisk_startaar}, "
        f"krysser NOK 100 i 2031 og tilsvarer ca. {((LAKSEPRIS_TREND[2040] / LAKSEPRIS_TREND[faktisk_startaar]) ** (1 / (2040 - faktisk_startaar)) - 1) * 100:.1f} %/år til 2040. "
        f"Modellen ligger {_midt['Modell vs trend']} mot trend i {int(_midt['Faktisk år'])} og {_sist['Modell vs trend']} i {int(_sist['Faktisk år'])}."
        f"{_tidlig}\n\n"
        f"*{LAKSEPRIS_KILDE}. Likevektsscenariene (konservativ 2,0 %, basis 6,1 %, "
        f"stramt tilbud 8,9 % nominell prisvekst fra 86,3 kr/kg i 2025) vises i tabellen under.*"
    )
    with st.expander("Vis tallene år for år (modell mot trend og syklisk estimat)", expanded=False):
        st.dataframe(tab, hide_index=True, use_container_width=True)


if __name__ == "__main__":
    fig = tegn_prisfigur(100.0, 2.0, 2030, 12)
    fig.savefig("prisfigur_test.png", dpi=110)
    print(sammenligningstabell(100.0, 2.0, 2026, 2030, 12).to_string(index=False))
