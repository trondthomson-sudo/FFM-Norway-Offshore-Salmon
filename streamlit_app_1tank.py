"""
streamlit_app_1tank.py - Biologibasert finansiell oppdrettsmodell (1 tank)
------------------------------------------------------------------------------
Startpunktet i den nye, større modellen: én tank, N oppskrifter i rotasjon,
solgt direkte som postsmolt. Viser BÅDE produksjonsplanen OG et
ressursregnskap (COGS-linjer, kg WFE-basert) per kohort - se
scheduler_1tank.py og resource_ledger.py for selve logikken.

Run:  python -m streamlit run streamlit_app_1tank.py
"""
import io
import re
import calendar
from datetime import date, timedelta

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import matplotlib.pyplot as plt

import config_1tank as default_config
from growth_tables import GrowthTables
from scheduler_1tank import build_1tank_schedule, week_label, monday_of_week
from scheduler_multitank import build_multitank_schedule
from resource_ledger import (
    build_resource_ledger, summarize_by_cohort, summarize_by_month, summarize_by_year,
    build_monthly_overview, build_cashflow_ledger, summarize_cashflow_by_period, build_per_kg,
    build_fixed_costs_weekly, build_konsolidert_kontantstrom, build_escalation_table,
    build_isolert_batch_projeksjon, build_renter_avdrag_per_uke, build_resultatregnskap,
    build_matchet_kostnad_per_uke, build_balanse, build_batch_resultat_og_balanse,
    build_batch_ukentlig_kostnad, build_renter_avdrag_per_ar, build_utleier_lonnsomhet,
    build_utleier_irr, interpoler_fiskeverdi_kr_per_kg, escalate_price_by_year, build_utleier_regnskap,
)
from formatting import fmt_int, fmt_float, parse_number, with_thousands, month_label, annuitet_manedsbelop, annuitetsplan

def _auto_format_number_input(label, key, default_value, help_text=None, min_value=0.0, container=None):
    """Text-input som viser tusenskiller (mellomrom), ingen desimaler - for
    STORE tall (tankvolum, smoltantall osv.), IKKE årstall. Reformateres til
    f.eks. '20 500' sa snart feltet mister fokus (samme mønster som i
    streamlit_app.py sin _auto_format_number_input). `container` lar deg
    plassere feltet i en st.columns()-kolonne i stedet for direkte i st/sidebar."""
    target = container if container is not None else st
    if key not in st.session_state:
        st.session_state[key] = fmt_int(default_value)

    def _reformat():
        val = max(min_value, parse_number(st.session_state[key], default=default_value))
        st.session_state[key] = fmt_int(val)

    target.text_input(label, key=key, on_change=_reformat, help=help_text)
    return max(min_value, parse_number(st.session_state[key], default=default_value))


def _nok_input(label, key, default_value, container=None, help_text=None):
    """NOK-beløp i sidepanelet: tusenskille (mellomrom) legges på automatisk
    når feltet mister fokus - også på tall brukeren selv skriver inn."""
    return _auto_format_number_input(label, key=key, default_value=float(default_value),
                                     min_value=0.0, container=container, help_text=help_text)


def _format_ledger_display(df: pd.DataFrame) -> pd.DataFrame:
    """Legger på tusenskiller (mellomrom) og fjerner unødvendige desimaler
    på tallkolonnene - både de faste (biomasse, antall_fisk, kg_wfe_produsert)
    og ALLE "mengde_<id>"/"kr_<id>"-kolonnene fra RESOURCES-listen, slik at
    nye ressurslinjer plukkes opp automatisk uten kodeendring her.
    'None' (ubetalte kr-felt) vises som tomt i stedet for teksten 'None'."""
    fixed_int_like = ["antall_fisk"]
    fixed_float_0dec = ["biomasse_kg", "vekt_g", "kg_wfe_brutto", "kg_wfe_dodelighet",
                        "kg_wfe_netto", "kg_wfe_netto_akkumulert", "kg_wfe_levert", "kg_wfe_levert_akkumulert",
                        "kg_solgt", "kg_solgt_akkumulert",
                        "inntekt_kr", "kostnad_totalt_kr", "netto_kontantstrom_kr", "akkumulert_kontantstrom_kr"]
    dynamic_0dec = [c for c in df.columns if c.startswith("mengde_") or c.startswith("kr_")]

    df = with_thousands(df, int_cols=[c for c in fixed_int_like if c in df.columns],
                         float_cols=[c for c in fixed_float_0dec + dynamic_0dec if c in df.columns],
                         float_decimals=0)
    for col in df.columns:
        df[col] = df[col].apply(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else v)
    return df


def _transpose_for_display(df: pd.DataFrame, period_col: str, period_labels=None) -> pd.DataFrame:
    """Snur en tidy (én-rad-per-periode) tabell til horisontalt format:
    ressurslinje/felt som RADER, periode (uke/måned/år/kohort) som
    KOLONNER - samme "wide"-stil som ukekalenderen ellers i Hexacage-
    prosjektet (jf. scheduler_1tank._assemble_wide_table). Formaterer
    tallene FØR transponering (så tusenskille/tomme kr-felt følger med)."""
    formatted = _format_ledger_display(df)
    if period_labels is not None:
        formatted[period_col] = period_labels
    wide = formatted.set_index(period_col).T
    wide.index.name = "Felt"
    return wide


def _render_table(df: pd.DataFrame, highlight_rows: list | None = None,
                   highlight_color: str = "#eef1f6", highlight_text_color: str = "#5b6b82",
                   highlight_groups: list | None = None, fixed_label_width_px: int | None = None,
                   fixed_data_col_width_px: int | None = None):
    """Rendrer en tabell som ekte HTML, høyrestilt, MED 'white-space: nowrap'
    på datacellene (slik at store tall som '65 687' aldri brytes over to
    linjer, uansett hvor mange kolonner/uker tabellen har), pakket inn i en
    beholder med egen horisontal scroll. st.table alene bryter tall-celler
    når det er mange smale kolonner (f.eks. ukentlig ressursregnskap med
    100+ uker) - denne unngår det, samtidig som selve siden ikke blir
    kjempebred (scroll skjer INNI boksen, ikke på hele siden).

    `highlight_rows`/`highlight_color`/`highlight_text_color`: valgfri
    ENKEL fargegruppe (bakgrunn OG tekstfarge på RADNAVN etter omdøping til
    lesbare etiketter) - brukt for å markere f.eks. at 13.1-13.6 er
    underlinjer til 13, ikke jevnbyrdige hovedrader i tabellen.

    `highlight_groups`: valgfri liste med FLERE fargegrupper samtidig, hver
    som dict {"rows": [...radnavn...], "bg": "#...", "text": "#..."} - brukt
    når man trenger to eller flere ULIKE fargesjatteringer i samme tabell
    (f.eks. én farge for underlinjer, en helt annen for "per kg"-rader, slik
    at absolutte kr-tall og per-kg-tall er lette å skille visuelt fra
    hverandre). Rader nevnt i FLERE grupper får sin farge fra SISTE gruppe
    i listen (senere overstyrer tidligere).

    `fixed_label_width_px`: valgfri FAST bredde (piksler) på "Felt"-kolonnen
    (radnavnene). Brukes når FLERE tabeller vises rett under hverandre og
    skal ha datakolonnene på linje vertikalt (uke-for-uke-sammenligning) -
    uten dette får hver tabell sin egen "Felt"-bredde basert på sine egne
    (ofte ulikt lange) radnavn, og kolonnene sklir ut av linje med
    hverandre. Radnavn får 'white-space: normal' (kan brytes over flere
    linjer) i stedet for 'nowrap', slik at lange navn ikke sprenger den
    faste bredden.

    `fixed_data_col_width_px`: valgfri FAST bredde (piksler) på HVER
    DATAKOLONNE (år/måned/uke) - brukes SAMMEN med `fixed_label_width_px`
    når flere tabeller (f.eks. Konsolidert kontantstrøm / Resultatregnskap /
    Balanse, alle i Årsoversikt) skal stå rett under hverandre med
    IDENTISK kolonnebredde år for år. Uten dette tilpasser hver tabell sine
    datakolonner til sitt eget innhold (f.eks. blir '121 752 385' bredere
    enn '1 980 000'), slik at samme år ender opp på ulik horisontal
    posisjon fra tabell til tabell - vanskelig å sammenligne visuelt selv
    om tallene i seg selv stemmer."""
    styler = (
        df.style
        .set_properties(**{"text-align": "right", "white-space": "nowrap", "padding": "2px 10px"})
        .set_table_styles([
            {"selector": "th", "props": [("white-space", "nowrap"), ("padding", "2px 10px")]},
            # FRYST radnavn-kolonne ("Felt"): henger fast til venstre ved
            # horisontal scroll (som "frys ruter" i Excel), så man alltid ser
            # hva hver linje er - også langt ute i uke 40 eller år 2035.
            # Trenger ugjennomsiktig bakgrunn, ellers "blør" datacellene
            # gjennom; highlight-fargene (apply_index) legges inline og vinner.
            {"selector": "th.row_heading", "props": [
                ("position", "sticky"), ("left", "0"), ("z-index", "2"), ("background-color", "#ffffff"),
                ("text-align", "left"),
            ]},
            {"selector": "th.index_name, th.blank", "props": [
                ("position", "sticky"), ("left", "0"), ("z-index", "3"), ("background-color", "#ffffff"),
            ]},
        ])
    )
    if fixed_label_width_px or fixed_data_col_width_px:
        styler = styler.set_table_styles([
            {"selector": "table", "props": [("table-layout", "fixed")]},
        ], overwrite=False)
    if fixed_label_width_px:
        styler = styler.set_table_styles([
            {"selector": "th.row_heading", "props": [
                ("white-space", "normal"), ("padding", "2px 10px"),
                ("width", f"{fixed_label_width_px}px"), ("min-width", f"{fixed_label_width_px}px"),
                ("max-width", f"{fixed_label_width_px}px"),
            ]},
        ], overwrite=False)
    if fixed_data_col_width_px:
        styler = styler.set_table_styles([
            {"selector": "th.col_heading", "props": [
                ("width", f"{fixed_data_col_width_px}px"), ("min-width", f"{fixed_data_col_width_px}px"),
                ("max-width", f"{fixed_data_col_width_px}px"),
            ]},
            {"selector": "td", "props": [
                ("width", f"{fixed_data_col_width_px}px"), ("min-width", f"{fixed_data_col_width_px}px"),
                ("max-width", f"{fixed_data_col_width_px}px"),
            ]},
        ], overwrite=False)

    grupper = list(highlight_groups) if highlight_groups else []
    if highlight_rows:
        grupper.append({"rows": highlight_rows, "bg": highlight_color, "text": highlight_text_color})

    if grupper:
        row_til_stil = {}
        for gruppe in grupper:  # senere grupper overstyrer tidligere for samme radnavn
            stil = f"background-color: {gruppe['bg']}; color: {gruppe['text']};"
            for rad in gruppe["rows"]:
                row_til_stil[rad] = stil

        def _highlight(row):
            stil = row_til_stil.get(row.name)
            return [stil] * len(row) if stil else [""] * len(row)

        def _highlight_index(s):
            return [row_til_stil.get(v, "") for v in s]

        styler = styler.apply(_highlight, axis=1).apply_index(_highlight_index)
    html = styler.to_html()
    st.markdown(f'<div style="overflow-x:auto; max-width:100%;">{html}</div>', unsafe_allow_html=True)


def _batch_sort_key(bid: str):
    """'K1-B8' -> (1, 8) - kronologisk sortering (kohort-nummer, deretter
    batch-nummer) av underradene i den ekspanderbare kontantstrøm-tabellen,
    i stedet for alfabetisk (som ville gitt K10 før K2)."""
    m = re.match(r"K(\d+)-B(\d+)", str(bid))
    return (int(m.group(1)), int(m.group(2))) if m else (10**9, 0)


def _build_kontantstrom_breakdown(cashflow: pd.DataFrame, batch_ukentlig: pd.DataFrame, period_kind: str,
                                   kr_cost_cols: list) -> pd.DataFrame:
    """(periode, batch_id) -> inntekt_kr (EKTE, batch-spesifikk) + kr_cost_cols
    (uke-avhengig proporsjonalt allokert) - grunnlaget for batch-detaljradene
    under hver ekspanderbar rad i Konsolidert kontantstrøm.

    - `inntekt_kr`: hentes RÅ per batch_id fra cashflow. Ekte, batch-
      spesifikk hendelse - pengene kommer inn nøyaktig i DEN batchens
      leveringsuke, ingen deling gir mening her.
    - `kr_cost_cols`: hentes fra `batch_ukentlig` (build_batch_ukentlig_
      kostnad() i resource_ledger.py) - SAMME kjernefunksjon som driver
      matchet kostnad i Resultatregnskap/Balanse, så alle tre tabellene
      er garantert konsistente med hverandre. Se den funksjonens docstring
      for selve fordelingsprinsippet (uke-avhengig: kun batcher som ikke
      er høstet ennå, antall for smolt/biomasse for resten).

    Summen over batchene, per periode og linje, er UENDRET fra før
    (andelene blant de gjenværende batchene summerer alltid til 1,0) - kun
    FORDELINGEN mellom batchene endres, så batch-radene fortsatt summerer
    eksakt til foreldreradens tall."""
    df = cashflow.copy()
    bu = batch_ukentlig.copy()
    if period_kind == "uke":
        df["periode"] = df["uke"]
        bu["periode"] = bu["uke"]
    elif period_kind == "maned":
        df["periode"] = pd.to_datetime(df["dato"]).dt.strftime("%Y-%m")
        bu["periode"] = pd.to_datetime(bu["dato"]).dt.strftime("%Y-%m")
    else:  # "ar"
        df["periode"] = pd.to_datetime(df["dato"]).dt.isocalendar().year
        bu["periode"] = pd.to_datetime(bu["dato"]).dt.isocalendar().year

    inntekt_grp = df.groupby(["periode", "batch_id"], sort=False)["inntekt_kr"].sum().reset_index()
    kost_grp = (bu.groupby(["periode", "batch_id"], sort=False)[kr_cost_cols].sum().reset_index()
                if len(bu) else pd.DataFrame(columns=["periode", "batch_id"] + kr_cost_cols))

    return inntekt_grp.merge(kost_grp, on=["periode", "batch_id"], how="outer").fillna(0.0)


def _build_resultatregnskap_breakdown(matchet_kostnad: pd.DataFrame, cashflow: pd.DataFrame,
                                       start_year: int, start_week: int, period_kind: str) -> pd.DataFrame:
    """(periode, batch_id) -> inntekt_kr (EKTE) + varekostnad_kr/lonnskostnader_kr
    (MATCHET - fra build_matchet_kostnad_per_uke(), bokført i sin helhet i
    batchens EGEN leveringsuke) + bruttofortjeneste_kr (derivert) -
    grunnlaget for batch-detaljradene i Resultatregnskap.

    Til forskjell fra Konsolidert kontantstrøm sitt batch-nedtrekk (som
    sprer kostnaden UTOVER vekstukene) samler dette HELE batchens
    opparbeidede kostnad i ÉN uke - selve poenget med "matching"-prinsippet
    (kostnad bokføres samtidig med den tilhørende inntekten)."""
    lonns_ider = {"annet_direkte_lonn", "indirekte_lonn"}
    varekost_kol = [c for c in matchet_kostnad.columns
                    if c.startswith("kr_") and c.endswith("_matchet")
                    and c[3:-8] not in lonns_ider]
    lonn_kol = [c for c in matchet_kostnad.columns
                if c.startswith("kr_") and c.endswith("_matchet") and c[3:-8] in lonns_ider]

    mk = matchet_kostnad.copy()
    uke_labels, uke_datoer = zip(*[week_label(w, start_year, start_week) for w in mk["uke_idx"]]) if len(mk) else ((), ())
    mk["uke"] = uke_labels
    mk["dato"] = uke_datoer
    if period_kind == "uke":
        mk["periode"] = mk["uke"]
    elif period_kind == "maned":
        mk["periode"] = pd.to_datetime(mk["dato"]).dt.strftime("%Y-%m")
    else:  # "ar"
        mk["periode"] = pd.to_datetime(mk["dato"]).dt.isocalendar().year
    mk["varekostnad_kr"] = mk[varekost_kol].sum(axis=1) if varekost_kol else 0.0
    mk["lonnskostnader_kr"] = mk[lonn_kol].sum(axis=1) if lonn_kol else 0.0
    kost_grp = (mk.groupby(["periode", "batch_id"], sort=False)[["varekostnad_kr", "lonnskostnader_kr"]]
                .sum().reset_index())

    cf = cashflow.copy()
    if period_kind == "uke":
        cf["periode"] = cf["uke"]
    elif period_kind == "maned":
        cf["periode"] = pd.to_datetime(cf["dato"]).dt.strftime("%Y-%m")
    else:
        cf["periode"] = pd.to_datetime(cf["dato"]).dt.isocalendar().year
    inntekt_grp = cf.groupby(["periode", "batch_id"], sort=False)["inntekt_kr"].sum().reset_index()

    out = inntekt_grp.merge(kost_grp, on=["periode", "batch_id"], how="outer").fillna(0.0)
    out["bruttofortjeneste_kr"] = out["inntekt_kr"] - out["varekostnad_kr"]
    return out


def _build_balanse_breakdown(batch_ukentlig: pd.DataFrame, matchet_kostnad: pd.DataFrame, cashflow: pd.DataFrame,
                              all_weeks_uke_dato: list, start_year: int, start_week: int, period_kind: str,
                              kundefrist_uker: int, leverandorfrist_uker: int) -> pd.DataFrame:
    """(periode, batch_id) -> biologisk_eiendel, kundefordringer, leverandorgjeld
    per batch - grunnlaget for batch-detaljradene i Balanse.

    KUN disse tre linjene lar seg meningsfullt dekomponere per batch:
      - Biologisk eiendel: batchens EGEN kumulerte påløpte (uke-avhengige,
        se build_batch_ukentlig_kostnad()) kostnad MINUS dens egen
        kumulerte matchede kostnad - går i null nøyaktig ved batchens egen
        levering, akkurat som i den isolerte per-batch-balansen.
      - Kundefordringer: rullerende sum av batchens EGEN reelle inntekt de
        siste `kundefrist_uker` ukene (inntekt er allerede en ekte,
        batch-spesifikk hendelse).
      - Leverandørgjeld: rullerende sum av batchens EGEN påløpte kostnad de
        siste `leverandorfrist_uker` ukene.

    Kontanter, Opptjent egenkapital og sum-/differanse-linjene er BEVISST
    IKKE med her - de er hele-driften-størrelser (kontanter er felles for
    alle kohorter/batcher, opptjent egenkapital er en kumulativ resultat-
    størrelse for hele anlegget) og lar seg ikke splitte per batch på noen
    meningsfull måte, uansett fordelingsnøkkel.

    Balansen er en SNAPSHOT (beholdning), ikke en flow (strøm) - så for
    måneds-/årsvisning tas SISTE ukes verdi i perioden, ikke en sum over
    perioden (samme prinsipp som build_balanse() sin egen måneds-/
    årsgruppering lenger nede i streamlit_app_1tank.py)."""
    alle_uker_labels = [u for u, _ in all_weeks_uke_dato]
    uke_til_dato = dict(all_weeks_uke_dato)

    # ---- Biologisk eiendel: kumulert påløpt (per batch) minus kumulert matchet ----
    resource_kr_cols = [c for c in batch_ukentlig.columns if c.startswith("kr_")]
    bu = batch_ukentlig.copy()
    bu["palopt_totalt"] = bu[resource_kr_cols].sum(axis=1) if resource_kr_cols else 0.0
    palopt_pivot = bu.pivot_table(index="uke", columns="batch_id", values="palopt_totalt",
                                   aggfunc="sum", fill_value=0.0)
    palopt_pivot = palopt_pivot.reindex(alle_uker_labels, fill_value=0.0)

    matchet_kr_cols = [c for c in matchet_kostnad.columns if c.endswith("_matchet")]
    mk = matchet_kostnad.copy()
    mk["matchet_totalt"] = mk[matchet_kr_cols].sum(axis=1) if matchet_kr_cols else 0.0
    mk["uke"] = [week_label(w, start_year, start_week)[0] for w in mk["uke_idx"]]
    matchet_pivot = mk.pivot_table(index="uke", columns="batch_id", values="matchet_totalt",
                                    aggfunc="sum", fill_value=0.0)
    matchet_pivot = matchet_pivot.reindex(alle_uker_labels, fill_value=0.0)

    alle_batcher = sorted(set(palopt_pivot.columns) | set(matchet_pivot.columns))
    palopt_pivot = palopt_pivot.reindex(columns=alle_batcher, fill_value=0.0)
    matchet_pivot = matchet_pivot.reindex(columns=alle_batcher, fill_value=0.0)
    biologisk_eiendel = (palopt_pivot.cumsum() - matchet_pivot.cumsum()).clip(lower=0)

    # ---- Kundefordringer: rullerende sum av batchens egen REELLE inntekt ----
    cf = cashflow.copy()
    inntekt_pivot = cf.pivot_table(index="uke", columns="batch_id", values="inntekt_kr",
                                    aggfunc="sum", fill_value=0.0)
    inntekt_pivot = inntekt_pivot.reindex(alle_uker_labels, fill_value=0.0).reindex(columns=alle_batcher, fill_value=0.0)
    kundefordringer = (inntekt_pivot.rolling(window=max(kundefrist_uker, 1), min_periods=1).sum()
                       if kundefrist_uker > 0 else inntekt_pivot * 0.0)

    # ---- Leverandørgjeld: rullerende sum av batchens egen påløpte kostnad ----
    leverandorgjeld = (palopt_pivot.rolling(window=max(leverandorfrist_uker, 1), min_periods=1).sum()
                       if leverandorfrist_uker > 0 else palopt_pivot * 0.0)

    # ---- Periode-tildeling + SNAPSHOT (siste uke i perioden, ikke sum) ----
    dato_series = pd.Series([uke_til_dato[u] for u in alle_uker_labels], index=alle_uker_labels)
    if period_kind == "uke":
        periode_verdier = alle_uker_labels
    elif period_kind == "maned":
        periode_verdier = pd.to_datetime(dato_series).dt.strftime("%Y-%m").tolist()
    else:  # "ar"
        periode_verdier = pd.to_datetime(dato_series).dt.isocalendar().year.tolist()

    def _siste_i_periode(pivot: pd.DataFrame, kolonnenavn: str) -> pd.DataFrame:
        d = pivot.copy()
        d["periode"] = periode_verdier
        snap = d.groupby("periode", sort=False).last()
        return snap.reset_index().melt(id_vars="periode", var_name="batch_id", value_name=kolonnenavn)

    out = _siste_i_periode(biologisk_eiendel, "biologisk_eiendel")
    out = out.merge(_siste_i_periode(kundefordringer, "kundefordringer"), on=["periode", "batch_id"])
    out = out.merge(_siste_i_periode(leverandorgjeld, "leverandorgjeld"), on=["periode", "batch_id"])
    return out


def _render_expandable_kontantstrom(wide_df: pd.DataFrame, highlight_groups: list,
                                     felt_bredde_px: int, kol_bredde_px: int,
                                     expandable: dict, breakdown: pd.DataFrame, periode_raw_order: list):
    """Egen HTML-tabellrenderer (bygger raden selv i stedet for å gå via
    pandas Styler som _render_table gjør) - lar UTVALGTE rader ('expandable')
    få en liten klikkbar trekant (▶/▼) som slår AV/PÅ synligheten til en
    gruppe underrader, én per batch - Excel-outline-stil. Ren klient-side
    (innebygde onclick-handlere direkte i HTML-en, ingen Streamlit-rundtur
    eller server-kall) - alle grupper starter KOLLAPSET.

    `expandable`: dict {radnavn (slik det står i wide_df sin indeks): rå
    kr-kolonnenavn i `breakdown`} for radene som skal kunne utvides -
    typisk Inntekt, hver av ressurslinjene 0-12, Variabel kostnad totalt og
    Dekningsbidrag (IKKE 'per solgt kg'/indeks-radene - de er forhold/
    ratioer og lar seg ikke meningsfullt dekomponere batch for batch).
    `breakdown`: output fra _build_kontantstrom_breakdown_by_batch().
    `periode_raw_order`: RÅ periode-verdier i SAMME rekkefølge som
    wide_df sine kolonner - nøkkelen `breakdown` slås opp med, siden
    wide_df sine kolonneoverskrifter kan være pyntede visningslabels
    (f.eks. 'Mar 2027') mens `breakdown` bruker de rå verdiene ('2027-03')."""
    row_til_stil = {}
    for gruppe in (highlight_groups or []):
        stil = f"background-color: {gruppe['bg']}; color: {gruppe['text']};"
        for rad in gruppe["rows"]:
            row_til_stil[rad] = stil

    cols = list(wide_df.columns)

    def _cell_style(extra: str = "") -> str:
        return (f"text-align:right; white-space:nowrap; padding:2px 10px; "
                f"width:{kol_bredde_px}px; min-width:{kol_bredde_px}px; max-width:{kol_bredde_px}px; {extra}")

    # STICKY: 'Felt'-kolonnen (radnavn) henger fast til VENSTRE ved
    # horisontal scroll, og hele toppraden (ukenumrene) henger fast ØVERST
    # ved vertikal scroll (aktuelt når mange batch-rader er ekspandert) -
    # samme prinsipp som "frys ruter" i Excel. Sticky-elementer trenger en
    # ugjennomsiktig bakgrunn (ellers "blør" innholdet som scrolles under
    # gjennom), derfor 'background:#ffffff' som base her - highlight-fargen
    # (`stil`) legges PÅ ETTERPÅ i selve radene under og vinner siden den
    # kommer sist i style-attributten.
    label_style = (f"text-align:left; white-space:normal; padding:2px 10px; "
                   f"width:{felt_bredde_px}px; min-width:{felt_bredde_px}px; max-width:{felt_bredde_px}px; "
                   f"position:sticky; left:0; z-index:2; background:#ffffff;")

    # Batch-nedtrekksceller bruker en CSS-klasse i stedet for inline-stil
    # per celle - med 6 tanker x 12 år er det 600+ batcher x 680 uker per
    # ekspanderbar rad, og inline-stil på hver <td> ga flere hundre MB HTML.
    html = [f'<style>.bd-c{{{_cell_style()} color:#9a9a9a;}}</style>',
            '<table style="border-collapse:collapse; table-layout:fixed;">',
            '<thead><tr>',
            f'<th style="{label_style} text-align:left; position:sticky; top:0; left:0; z-index:4;">Felt</th>']
    for c in cols:
        html.append(f'<th style="{_cell_style()} font-weight:600; position:sticky; top:0; z-index:3; '
                     f'background:#ffffff;">{c}</th>')
    html.append('</tr></thead><tbody>')

    for i, radnavn in enumerate(wide_df.index):
        stil = row_til_stil.get(radnavn, "")
        is_expandable = radnavn in expandable
        group_class = f"bd-grp-{i}"

        toggle_html = ""
        th_onclick = ""
        th_cursor = ""
        if is_expandable:
            # Klikk-handleren ligger nå på HELE <th>-cellen (radnavnet), ikke
            # bare den vesle ▶-trekanten - mye lettere å treffe. Selve
            # pil-glyfen oppdateres via en stabil id (arrow_id) siden 'this'
            # i onclick nå peker på <th>-elementet, ikke pil-spennet.
            arrow_id = f"arrow-{group_class}"
            th_onclick = (
                f'onclick="var els=document.getElementsByClassName(\'{group_class}\'); '
                f'for(var j=0;j<els.length;j++){{els[j].style.display = '
                f'(els[j].style.display===\'none\'?\'\':\'none\');}} '
                f'var a=document.getElementById(\'{arrow_id}\'); '
                f'a.textContent = (a.textContent===\'\u25b6\'?\'\u25bc\':\'\u25b6\');" '
            )
            th_cursor = "cursor:pointer; user-select:none;"
            toggle_html = f'<span id="{arrow_id}" style="display:inline-block; width:14px;">\u25b6</span> '

        html.append('<tr>')
        html.append(f'<th style="{label_style} {stil} {th_cursor}" {th_onclick}>{toggle_html}{radnavn}</th>')
        for c in cols:
            v = wide_df.loc[radnavn, c]
            html.append(f'<td style="{_cell_style(stil)}">{v}</td>')
        html.append('</tr>')

        if is_expandable:
            raw_col = expandable[radnavn]
            relevant = breakdown[breakdown["periode"].isin(set(periode_raw_order))]
            totals = relevant.groupby("batch_id")[raw_col].sum()
            batch_ids = sorted((b for b in totals.index if abs(totals[b]) > 0.5), key=_batch_sort_key)
            pivot = relevant.pivot_table(index="batch_id", columns="periode", values=raw_col,
                                          aggfunc="sum", fill_value=0.0)
            # Tak på nedtrekkets størrelse: med Ukeoversikt over alle år og
            # mange tanker blir batcher x uker fort flere millioner celler -
            # det sprenger både minnet på Streamlit Cloud og nettleseren.
            # Viser da én forklarende rad i stedet for selve nedtrekket.
            MAKS_NEDTREKK_CELLER = 200_000
            if batch_ids and len(batch_ids) * len(cols) > MAKS_NEDTREKK_CELLER:
                html.append(f'<tr class="{group_class}" style="display:none;">')
                html.append(
                    f'<th style="{label_style} padding-left:26px; font-weight:normal; '
                    f'font-style:italic; color:#8a8a8a;">{len(batch_ids)} batcher x {len(cols)} perioder er for '
                    f'mye til å vise per batch her - velg Måneds-/Årsoversikt, eller Ukeoversikt for ett år</th>'
                )
                for c in cols:
                    html.append('<td class="bd-c"></td>')
                html.append('</tr>')
                batch_ids = []
            elif not batch_ids:
                # VIKTIG: uten dette ser et klikk ut som det "ikke virker" - pilen
                # snur, men ingen rader dukker opp, fordi det rett og slett ikke
                # FINNES noen batch med et beløp > 0 kr i akkurat den perioden som
                # vises nå (f.eks. Kundefordringer i Årsoversikt, der 2 ukers
                # kundefrist nesten alltid er oppgjort før årsskiftet). Gi en
                # tydelig forklarende rad i stedet for stillhet.
                html.append(f'<tr class="{group_class}" style="display:none;">')
                html.append(
                    f'<th style="{label_style} padding-left:26px; font-weight:normal; '
                    f'font-style:italic; color:#8a8a8a;">Ingen batcher med beløp &gt; 0 i valgt periode</th>'
                )
                for c in cols:
                    html.append(
                        f'<td style="{_cell_style()} font-style:italic; color:#b5b5b5;">'
                        f'(prøv Uke- eller Månedsoversikt)</td>'
                    )
                html.append('</tr>')
            for bid in batch_ids:
                html.append(f'<tr class="{group_class}" style="display:none;">')
                html.append(
                    f'<th style="{label_style} padding-left:26px; font-weight:normal; color:#8a8a8a;">'
                    f'\u21b3 {bid}</th>'
                )
                for c, praw in zip(cols, periode_raw_order):
                    val = pivot.loc[bid, praw] if (bid in pivot.index and praw in pivot.columns) else 0.0
                    vis = fmt_int(val) if abs(val) > 0.5 else ""
                    html.append(f'<td class="bd-c">{vis}</td>')
                html.append('</tr>')

    html.append('</tbody></table>')

    # Scroll-verktøylinje: fire knapper som styrer den horisontale scrollen
    # i boksen under (id="kons-scroll-box") - "|◀ Start" og "Slutt ▶|"
    # hopper til hhv. første/siste kolonne, "◀"/"▶" flytter ett "sidesteg"
    # om gangen (STEP_PX, ca. 6 kolonner) - nødvendig fordi tabellen med
    # Ukeoversikt fort blir 600+ kolonner bred, og å dra i den native
    # nettleser-scrollbaren helt til uke 52 uten disse er upraktisk uten en
    # svært bred skjerm.
    step_px = kol_bredde_px * 6
    scroll_toolbar = f"""
    <div style="margin-bottom:6px; display:flex; gap:6px; font-family: 'Source Sans Pro', -apple-system, sans-serif;">
      <button onclick="document.getElementById('kons-scroll-box').scrollTo({{left:0, behavior:'smooth'}});"
              style="cursor:pointer; padding:4px 10px; border:1px solid #ccc; border-radius:4px; background:#f5f5f5;">|◀ Start</button>
      <button onclick="document.getElementById('kons-scroll-box').scrollBy({{left:-{step_px}, behavior:'smooth'}});"
              style="cursor:pointer; padding:4px 10px; border:1px solid #ccc; border-radius:4px; background:#f5f5f5;">◀</button>
      <button onclick="document.getElementById('kons-scroll-box').scrollBy({{left:{step_px}, behavior:'smooth'}});"
              style="cursor:pointer; padding:4px 10px; border:1px solid #ccc; border-radius:4px; background:#f5f5f5;">▶</button>
      <button onclick="var el=document.getElementById('kons-scroll-box'); el.scrollTo({{left:el.scrollWidth, behavior:'smooth'}});"
              style="cursor:pointer; padding:4px 10px; border:1px solid #ccc; border-radius:4px; background:#f5f5f5;">Slutt ▶|</button>
    </div>
    """

    # VIKTIG: st.markdown(unsafe_allow_html=True) FJERNER onclick-attributter
    # (og all annen JS) fra HTML-en som en sikkerhetsforanstaltning - derfor
    # skjedde det ingenting ved klikk med den forrige tilnærmingen. En ekte
    # <iframe> via components.html() saniterer IKKE innholdet, så onclick
    # faktisk kjører der.
    #
    # VIKTIG #2 (fix for at "frys topplinje" ikke virket): 'position:sticky'
    # fester seg til den NÆRMESTE beholderen som FAKTISK scroller - ikke
    # bare en beholder som har 'overflow-x:auto'. Forrige versjon hadde
    # ingen fast høyde på #kons-scroll-box, så boksen bare vokste i det
    # uendelige og det var IFRAMEN (utenfor boksen) som scrollet - sticky
    # hadde da ingenting reelt å feste seg til, og verktøylinjen (som lå
    # UTENFOR boksen) ble med i den scrollingen. Løsningen: gi boksen en
    # FAST maks-høyde med egen 'overflow-y:auto', slik at boksen selv blir
    # den ekte scroll-beholderen for BÅDE sticky-headeren og scroll-
    # knappene (som nå ligger over/utenfor boksen og derfor aldri beveger
    # seg, siden det er boksen - ikke iframen - som scroller).
    # Høyden er satt dypere enn tidligere som default (opptil 1100px, mot
    # 650px før) - når du ekspanderer flere batch-grupper vokser innholdet
    # fort forbi det opprinnelige (kollapsede) radantallet, og en for lav
    # boks tvinger frem mye intern scrolling. Fortsatt et TAK (ikke
    # ubegrenset) - resterende innhold scroller internt i boksen uansett.
    box_maks_hoyde_px = min(1100, max(550, 50 + 34 * len(wide_df.index)))
    full_html = f"""
    {scroll_toolbar}
    <div id="kons-scroll-box" style="font-family: 'Source Sans Pro', -apple-system, sans-serif; font-size: 14px;
                overflow-x:auto; overflow-y:auto; max-width:100%; max-height:{box_maks_hoyde_px}px;
                border:1px solid #e6e6e6;">
      {"".join(html)}
    </div>
    """
    iframe_height = box_maks_hoyde_px + 60  # +60 til verktøylinjen og litt luft
    components.html(full_html, height=iframe_height, scrolling=False)


st.set_page_config(page_title="Norway Offshore Salmon", layout="wide")
st.title("Norway Offshore Salmon")
st.caption(
    "Startpunkt for den nye modellen: én tank, N oppskrifter i rotasjon, "
    "solgt direkte som postsmolt. Ressursregnskapet nederst er bygget for å "
    "kunne utvides til flere tanker/kohorter/salgsbatcher uten omregning."
)

# ----------------------------------------------------------------------
# SIDEPANEL
# ----------------------------------------------------------------------
with st.sidebar:
    # Diskret kodefelt - IKKE tydelig merket som "lås opp IRR" (poenget er
    # at andre som ser skjermen ikke skal legge merke til at noe er skjult
    # her i det hele tatt). Riktig kode viser IRR-forutsetningene i
    # "Lønnsomhet - Utleier"-boksen under OG selve IRR-resultatet i
    # hovedinnholdet lenger ned - uten koden beregnes IRR fortsatt stille i
    # bakgrunnen (motoren kjører uendret), kun VISNINGEN er skjult.
    # Kodefeltet er FJERNET (bekreftet av bruker) - IRR-seksjonen for
    # utleier vises alltid.
    _vis_irr = True

    st.header("Salg")

    def _bruk_produkttype_variant():
        valg = st.session_state["produkttype_valg"]
        _jan_startdato = monday_of_week(0, default_config.START_ISO_YEAR, default_config.START_ISO_WEEK)
        presets = {
            "Postsmolt 2x": {
                "n_batches": 2, "rgi_pct": 115.0, "oppskrift1_dato": _jan_startdato, "salgspris": "90",
                "salgspris_modus": "Følger fiskeverditabellen", "prisprofil_valg": "Stabil pris (flat hele året)",
                "smolt_pris_modus": "Fiskeverditabell (interpolert)",
                "startw_0": 80.0, "smolt_0": fmt_int(1_900_000), "gw_0": 24, "cw_0": 2, "sw_0": 1,
                "startw_1": 60.0, "smolt_1": fmt_int(1_150_000), "gw_1": 24, "cw_1": 2, "sw_1": 1,
            },
            # 3x: TRE uavhengige kohorter (ulik vekt/antall per kohort,
            # akkurat som referansen) - IKKE lenger én oppskrift gjentatt.
            "Postsmolt 3x": {
                "n_batches": 3, "rgi_pct": 100.0, "oppskrift1_dato": _jan_startdato,
                "salgspris_modus": "Følger fiskeverditabellen", "prisprofil_valg": "Stabil pris (flat hele året)",
                "smolt_pris_modus": "Fiskeverditabell (interpolert)",
                "startw_0": 145.0, "smolt_0": fmt_int(2_000_000), "gw_0": 15, "cw_0": 2, "sw_0": 1,
                "startw_1": 150.0, "smolt_1": fmt_int(1_850_000), "gw_1": 15, "cw_1": 2, "sw_1": 1,
                "startw_2": 125.0, "smolt_2": fmt_int(1_750_000), "gw_2": 15, "cw_2": 2, "sw_2": 1,
            },
            # 4x: FIRE uavhengige kohorter, felles smoltvekt (100 g) men
            # ULIKT smoltantall per kohort - matcher referansetallene fra
            # "postsmolt_manuell_4x" (samme prinsipp: differensiert
            # smoltantall utnytter den ledige tetthetskapasiteten i kalde
            # sesonger bedre enn ett likt tall for alle fire rundene ville
            # gjort - se Hexacage_postsmolt_manuell.md).
            "Postsmolt 4x": {
                "n_batches": 4, "rgi_pct": 100.0, "oppskrift1_dato": _jan_startdato,
                "salgspris_modus": "Fast pris (kr/kg)",
                "smolt_pris_modus": "Fiskeverditabell (interpolert)",
                "startw_0": 100.0, "smolt_0": fmt_int(4_600_000), "gw_0": 11, "cw_0": 2, "sw_0": 1,
                "startw_1": 100.0, "smolt_1": fmt_int(3_000_000), "gw_1": 11, "cw_1": 2, "sw_1": 1,
                "startw_2": 100.0, "smolt_2": fmt_int(2_400_000), "gw_2": 11, "cw_2": 2, "sw_2": 1,
                "startw_3": 100.0, "smolt_3": fmt_int(3_500_000), "gw_3": 11, "cw_3": 2, "sw_3": 1,
            },
            # Slaktefisk = Big Dipper MULTI-TANK: N parallelle tanker, forskjøvet
            # innsett (uke 1 + 8 + 8 + ...), ÉN felles oppskrift for alle.
            # 50 vekstuker: 8 batcher, siste batch selges i uke 50 i tanken.
            "Slaktefisk": {
                "n_batches": 1, "rgi_pct": 100.0, "salgspris": "100",
                # én oppskrift per tank (6 stk) - samme startverdier, juster fritt per tank
                **{f"startw_{i}": 750.0 for i in range(8)},
                **{f"smolt_{i}": fmt_int(850_000) for i in range(8)},
                **{f"gw_{i}": 50 for i in range(8)}, **{f"cw_{i}": 1 for i in range(8)}, **{f"sw_{i}": 8 for i in range(8)},
                "n_tanks": int(getattr(default_config, "N_TANKS", 6)),
                "tank_stagger": int(getattr(default_config, "TANK_STAGGER_WEEKS", 8)),
                "startaar": int(default_config.START_ISO_YEAR), "startuke": int(default_config.START_ISO_WEEK),
                "smolt_pris_modus": "Fiskeverditabell (interpolert)",
                "salgspris_modus": "Fast pris (kr/kg)",
            },
        }
        for key, verdi in presets.get(valg, {}).items():
            st.session_state[key] = verdi

    produkttype_valg = st.radio(
        "Produkttype", options=["Postsmolt 2x", "Slaktefisk"],
        index=1 if default_config.PRODUKTTYPE == "Slaktefisk" else 0,
        key="produkttype_valg", on_change=_bruk_produkttype_variant,
        help="'Postsmolt 2x' bruker TO UAVHENGIGE oppskrifter (egen smoltvekt/antall/varighet per "
             "kohort, default like) - juster hver for seg under 'Rotasjon'. Rotasjonen (24+2 "
             "uker x 2) treffer nøyaktig 52 uker, så det er ingen kalenderdrift over tid. "
             "'Slaktefisk': disse kostnadene gjelder, selges som HOG (hodekappet vekt) i stedet "
             "for WFE (levendevekt). "
             "(Postsmolt 3x/4x er midlertidig fjernet fra sidepanelet - 51-ukers rotasjonen der "
             "ga en kalenderdrift over mange år som økte tettheten jevnt. Fortsatt i koden som "
             "presets hvis dere vil ta dem tilbake senere.)",
    )
    # VIKTIG: on_change over kjører KUN når radioknappen faktisk ENDRES ved
    # klikk - laster du siden på nytt mens "Slaktefisk" (eller hvilket som
    # helst valg) allerede står valgt fra en tidligere økt, kjører IKKE
    # presetet på nytt, og gamle feltverdier fra forrige gang blir stående
    # uansett hva som er endret i koden siden. Denne knappen tvinger gjennom
    # NÅVÆRENDE presets standardverdier uansett - trykk her hvis feltene
    # under viser noe annet enn det du forventer.
    if st.button(f"↺ Sett '{produkttype_valg}' til standardverdier", key="tving_preset_knapp"):
        _bruk_produkttype_variant()
        st.rerun()
    produkttype = "Slaktefisk" if produkttype_valg == "Slaktefisk" else "Postsmolt"
    if produkttype == "Slaktefisk":
        hog_faktor = st.number_input(
            "HOG-faktor (andel av WFE)", value=float(default_config.HOG_FAKTOR),
            min_value=0.0, max_value=1.0, step=0.005, format="%.3f", key="hog_faktor",
        )
        st.caption(f"Solgt vekt = levert WFE x {hog_faktor:.3f}. Salgsprisen under er da kr/kg HOG.")
    else:
        hog_faktor = 1.0
    st.caption("Brukes til inntektsraden i kontantstrømtabellen. La stå tom for å vise kontantstrøm uten inntekt.")
    if produkttype == "Slaktefisk":
        # Fiskeverditabellen gjelder KUN postsmolt (fisk som selges videre
        # til påvekst - verdien følger smoltvekten). Slaktefisk selges til
        # markedspris per kg HOG - ingen tabell-modus her.
        salgspris_modus = "Fast pris (kr/kg)"
    else:
        salgspris_modus = st.radio(
            "Salgspris-modell", options=["Fast pris (kr/kg)", "Følger fiskeverditabellen"],
            index=0, key="salgspris_modus",
            help="'Fast pris': ett flatt kr/kg-tall for HELE simuleringen, uansett leveringsvekt (som før). "
                 "'Følger fiskeverditabellen': salgsprisen slås opp PER BATCH, interpolert til DEN "
                 "batchens egen faktiske leveringsvekt - samme tabell og prinsipp som brukes for "
                 "smolt-INNKJØP (se 'Ressursregnskap' under). To batcher med ulik leveringsvekt får da "
                 "ulik kr/kg, ikke samme flate tall.",
        )
    salgspris_txt = st.text_input(
        f"Salgspris (kr/kg {'HOG' if produkttype == 'Slaktefisk' else 'WFE'} solgt)",
        value="100", key="salgspris",
        disabled=(salgspris_modus == "Følger fiskeverditabellen"),
    )
    if salgspris_modus == "Følger fiskeverditabellen":
        st.caption(
            "Feltet over ignoreres når fiskeverditabellen brukes - se 'Vis fiskeverditabellen' i "
            "Ressursregnskap-seksjonen for de faktiske tallene."
        )

    prisprofil_valg = st.radio(
        "Prisprofil", options=["Stabil pris (flat hele året)", "Sesongvariert (indeksert per uke)"],
        index=1 if default_config.USE_SEASONAL_PRICE_INDEX else 0, key="prisprofil_valg",
        help="'Sesongvariert' multipliserer salgsprisen over (etter evt. årlig eskalering) med en "
             "ukentlig indeks - årssnittet forblir likt salgsprisen SÅ LENGE indeksens eget snitt "
             "er 1,00 (garantert av normaliseringen appen gjør på tabellen under).",
    )
    bruk_sesongpris = prisprofil_valg.startswith("Sesongvariert")
    seasonal_index_runtime = None
    if bruk_sesongpris:
        st.caption(
            "Indeks per uke, snitt over de 52 ukene NORMALISERES automatisk til nøyaktig 1,00 - "
            "juster de rå tallene under fritt, snittet i bunnen oppdateres live. Default-verdiene "
            "er de faktiske ukentlige tallene fra NOS spot-pris-grafen (laks, 2011-2025)."
        )
        seasonal_default_df = pd.DataFrame({
            "Uke": list(range(1, 53)),
            "Indeks": [default_config.SEASONAL_PRICE_INDEX_BY_WEEK[u] for u in range(1, 53)],
        })
        seasonal_edited = st.data_editor(
            seasonal_default_df, hide_index=True, use_container_width=True,
            disabled=["Uke"], key="seasonal_price_editor", height=250,
        )
        seasonal_raw = {int(r["Uke"]): float(r["Indeks"]) for _, r in seasonal_edited.iterrows()}
        seasonal_snitt_edited = sum(seasonal_raw.values()) / len(seasonal_raw) if seasonal_raw else 1.0
        seasonal_index_runtime = (
            {u: v / seasonal_snitt_edited for u, v in seasonal_raw.items()} if seasonal_snitt_edited else seasonal_raw
        )
        st.caption(f"Snitt av tabellen over (før normalisering): {seasonal_snitt_edited:.4f} "
                   f"(normaliseres uansett til 1,0000 i beregningen).")

    st.header("Faste kostnader (konsolidert)")
    st.caption(
        "Uavhengige av batch/kohort - påløper hver kalenderuke uansett tankstatus. "
        "Kun relevante for konsolidert kontantstrøm (se egen seksjon nederst), ikke for "
        "enkeltbatch-visningene. La stå tom for å telle som 0."
    )
    fastkost_periode = st.radio(
        "Angi som", options=["Per uke", "Per år"], index=1, key="fastkost_periode", horizontal=True,
        help="'Per år' regnes automatisk om til kr/uke internt (delt på 52) - selve "
             "beregningene bruker alltid en ukentlig sats.",
    )
    fastkost_divisor = 1.0 if fastkost_periode == "Per uke" else 52.0
    fixed_cost_prices = {}

    with st.expander("13. Leie av Big Dipper-anlegg - underlinjer", expanded=False):
        st.caption("Summen av disse postene er det oppdretter skal betale Aqualoop - dette blir 13. Leie av Big Dipper-anlegg i faste kostnader under.")
        hx = dict(default_config.HEXACAGE_LEIE_DEFAULTS)

        st.markdown("**1) Kapitalleie**")
        c1, c2 = st.columns(2)
        capex = _nok_input("CAPEX (NOK)", "hx_capex", hx["capex_nok"], container=c1)
        kapitalleie_pct_txt = c2.text_input("Kapitalleie-sats (%)", value=fmt_float(hx["kapitalleie_pct"] * 100, 1), key="hx_kapitalleie_pct")
        kapitalleie_pct = parse_number(kapitalleie_pct_txt, hx["kapitalleie_pct"] * 100) / 100.0
        # Reforhandling av TC underveis (inntil to ganger): velg ÅR i rullegardin
        # og hvor stor andel av GAPET til nybyggparitet som skal hentes inn.
        # Selve den nye satsen regnes ut lenger ned (der eskaleringstabellen er
        # kjent) og vises i "Nybyggparitet"-tabellen under Utleier.
        _rp_ar_valg = ["Ikke i bruk"] + [str(y) for y in range(int(st.session_state.get("startaar", default_config.START_ISO_YEAR)) + 1,
                                                              int(st.session_state.get("startaar", default_config.START_ISO_YEAR)) + int(default_config.N_YEARS_TO_RUN) + 1)]
        reforhandlinger = []
        _rf_def = getattr(default_config, "TC_REFORHANDLING_DEFAULTS", [{"ar": None, "andel": 0.5}] * 2)
        st.caption("Reforhandling av TC (ny kapitalleie-sats) underveis - gjelder fra 1. januar valgt år. "
                   "Ny TC = dagens (eskalerte) TC + valgt andel av gapet opp til nybyggparitet det året.")
        for _k in range(2):
            _ca, _cb = st.columns(2)
            _def_ar = str(_rf_def[_k]["ar"]) if _rf_def[_k].get("ar") else "Ikke i bruk"
            _def_idx = _rp_ar_valg.index(_def_ar) if _def_ar in _rp_ar_valg else 0
            _rp_ar_txt = _ca.selectbox(f"Reforhandling {_k + 1}: år", options=_rp_ar_valg, index=_def_idx, key=f"hx_reforh_ar_{_k}")
            _rp_andel = _cb.number_input(f"Andel av gap til nybyggparitet (%)", min_value=0.0, max_value=200.0,
                                         value=float(_rf_def[_k].get("andel", 0.5)) * 100, step=5.0, format="%.0f", key=f"hx_reforh_andel_{_k}",
                                         help="100 % = helt opp til nybyggparitet, 50 % = halvveis, 0 % = uendret.")
            if _rp_ar_txt != "Ikke i bruk":
                reforhandlinger.append({"ar": int(_rp_ar_txt), "andel": _rp_andel / 100.0})
        kapitalleie_reprising = []   # fylles ut senere (år, sats) når eskaleringen er kjent
        _np = getattr(default_config, "NYBYGGPARITET_DEFAULTS", {"byggeindeks_pct_ar": 0.04, "ebitda_yield_pct": 0.12})
        st.markdown("**Nybyggparitet (guide for ny TC)**")
        _c1, _c2 = st.columns(2)
        byggeindeks_txt = _c1.text_input("Byggeindeks (%/år)", value=fmt_float(_np["byggeindeks_pct_ar"] * 100, 1), key="np_byggeindeks")
        ebitda_yield_txt = _c2.text_input("EBITDA-yield på nybyggpris (%)", value=fmt_float(_np["ebitda_yield_pct"] * 100, 1), key="np_yield")
        byggeindeks_pct_ar = parse_number(byggeindeks_txt, _np["byggeindeks_pct_ar"] * 100) / 100.0
        ebitda_yield_pct = parse_number(ebitda_yield_txt, _np["ebitda_yield_pct"] * 100) / 100.0
        st.caption(f"→ Nybyggpris = CAPEX x (1 + {byggeindeks_pct_ar*100:.1f} %)^år siden {int(default_config.START_ISO_YEAR)}; "
                   f"EBITDA-krav = nybyggpris x {ebitda_yield_pct*100:.1f} %; guide-TC = EBITDA-krav + årets driftskostnader i "
                   "leien. Tabellen ligger under 'Lønnsomhet - Utleier'.")
        kapitalleie_ar = capex * kapitalleie_pct
        st.caption(f"→ Kapitalleie: {fmt_int(kapitalleie_ar)} kr/år")

        st.markdown("**2) Oppankring (annuitetslån)**")
        c1, c2, c3 = st.columns(3)
        oppankring_inv = _nok_input("Investering (NOK)", "hx_oppankring_inv", hx["oppankring_investering_nok"], container=c1)
        oppankring_mnd = c2.number_input("Nedbetaling (mnd)", value=int(hx["oppankring_nedbetaling_maneder"]), min_value=1, key="hx_oppankring_mnd")
        oppankring_rente_txt = c3.text_input("Rente (%/år)", value=fmt_float(hx["oppankring_rente_pct_ar"] * 100, 1), key="hx_oppankring_rente")
        oppankring_rente = parse_number(oppankring_rente_txt, hx["oppankring_rente_pct_ar"] * 100) / 100.0
        oppankring_mnd_belop = annuitet_manedsbelop(oppankring_inv, oppankring_rente, int(oppankring_mnd))
        oppankring_ar = oppankring_mnd_belop * 12
        st.caption(f"→ Annuitet: {fmt_int(oppankring_mnd_belop)} kr/mnd = {fmt_int(oppankring_ar)} kr/år")

        st.markdown("**3) Teknisk vedlikehold (NY - del av leiebeløpet, gjennomfakturert kost)**")
        teknisk_vedlikehold_ar = _nok_input(
            "Teknisk vedlikehold (NOK/år, startår)", "hx_teknisk_vedlikehold",
            hx.get("teknisk_vedlikehold_nok_per_ar", 1_000_000.0),
        )
        st.caption(
            "→ Del av '13. Leie' (belastes oppdretter), og telles automatisk med i BÅDE "
            "Leieinntekter OG Driftskostnader i Lønnsomhet-Utleier-modellen lenger ned "
            "(går i null der - ren gjennomfakturering, samme prinsipp som de andre "
            "driftskostnad-linjene 13.4-13.10)."
        )

        st.markdown("**4) Årlig rengjøring innvendig**")
        rengj_innv_ar = _nok_input("Rengjøring innvendig (NOK/år)", "hx_rengj_innv", hx["rengjoring_innvendig_nok_per_ar"])

        st.markdown("**5) Årlig rengjøring av krager**")
        rengj_krager_ar = _nok_input("Rengjøring av krager (NOK/år)", "hx_rengj_krager", hx["rengjoring_krager_nok_per_ar"])

        st.markdown("**6) Desinfeksjon**")
        desinfeksjon_per_kohort_kr = _nok_input("Desinfeksjon (NOK per kohort/generasjon)", "hx_desinfeksjon", hx["desinfeksjon_nok_per_kohort"])
        st.caption(
            "→ Engangsbeløp PER KOHORT (ikke en jevn ukentlig/årlig sats) - påløper kun i "
            "uken en ny kohort settes inn. Beregnes ut fra faktisk antall kohorter i "
            "simuleringen, vises i 'Konsolidert kontantstrøm' nederst - ikke lagt inn i "
            "kr/år-summen under."
        )

        st.markdown("**7) Lønn lokalitet**")
        c1, c2 = st.columns(2)
        lonn_lok_per_person = _nok_input("Lønn lokalitet (NOK/år, per person)", "hx_lonn_lok", hx["lonn_lokalitet_nok_per_ar"], container=c1)
        lonn_lok_antall = c2.number_input("Antall lokalitet", value=int(hx["lonn_lokalitet_antall"]), min_value=0, key="hx_lonn_lok_antall")
        lonn_lok_ar = lonn_lok_per_person * lonn_lok_antall

        st.markdown("**8) Lønn land**")
        c1, c2 = st.columns(2)
        lonn_land_per_person = _nok_input("Lønn land (NOK/år, per person)", "hx_lonn_land", hx["lonn_land_nok_per_ar"], container=c1)
        lonn_land_antall = c2.number_input("Antall land", value=int(hx["lonn_land_antall"]), min_value=0, key="hx_lonn_land_antall")
        lonn_land_ar = lonn_land_per_person * lonn_land_antall

        sosiale_pct_txt = st.text_input("Sosiale kostnader (% av lønn lokalitet + land)", value=fmt_float(hx["sosiale_kostnader_pct"] * 100, 1), key="hx_sosiale_pct")
        sosiale_pct = parse_number(sosiale_pct_txt, hx["sosiale_kostnader_pct"] * 100) / 100.0
        sosiale_lok_ar = lonn_lok_ar * sosiale_pct
        sosiale_land_ar = lonn_land_ar * sosiale_pct
        sosiale_ar = sosiale_lok_ar + sosiale_land_ar
        st.caption(
            f"→ Lønn lokalitet {fmt_int(lonn_lok_ar)} (+ {fmt_int(sosiale_lok_ar)} sosiale kostnader) "
            f"+ lønn land {fmt_int(lonn_land_ar)} (+ {fmt_int(sosiale_land_ar)} sosiale kostnader)"
        )

        st.markdown("**9) Forsikring anlegg og fartøy**")
        forsikring_pct_txt = st.text_input(
            "Forsikring-sats (% av CAPEX)", value=fmt_float(hx["forsikring_pct"] * 100, 2), key="hx_forsikring_pct",
        )
        forsikring_pct = parse_number(forsikring_pct_txt, hx["forsikring_pct"] * 100) / 100.0
        forsikring_ar = capex * forsikring_pct
        st.caption(f"→ Forsikring: {fmt_int(capex)} x {forsikring_pct*100:.2f} % = {fmt_int(forsikring_ar)} kr/år")

        st.markdown("**10) ADK (andre driftskostnader)**")
        adk_ar = _nok_input("ADK (NOK/år)", "hx_adk", hx["adk_nok_per_ar"])

        leie_hexacage_ar = (kapitalleie_ar + oppankring_ar + teknisk_vedlikehold_ar + rengj_innv_ar + rengj_krager_ar +
                            lonn_lok_ar + lonn_land_ar + sosiale_ar + forsikring_ar + adk_ar)
        st.markdown(f"**Sum - 13. Leie av Big Dipper-anlegg (ekskl. desinfeksjon): {fmt_int(leie_hexacage_ar)} kr/år**")

    with st.expander("Lønnsomhet - Utleier (Big Dipper/Aqualoop) - forutsetninger", expanded=False):
        st.caption(
            "Egen lønnsomhetsmodell for UTLEIER (eier av anlegget), atskilt fra oppdretters "
            "P&L/balanse ellers i appen - se full forklaring under overskriften lenger ned. "
            "Utleiers EGEN banklånsfinansiering av CAPEX, skattesats og vedlikeholdsinvestering "
            "under er HELT NYE antakelser, ikke bekreftet mot faktiske lånevilkår."
        )
        ux = dict(default_config.UTLEIER_DEFAULTS)
        c1, c2, c3 = st.columns(3)
        ebitda_mult_txt = c1.text_input("Belåning (x EBITDA)", value=fmt_float(ux["ebitda_multipel"], 1), key="ux_ebitda_mult")
        swap_txt = c2.text_input("Swap-rente (%/år)", value=fmt_float(ux["swap_rente_pct"] * 100, 2), key="ux_swap")
        paslag_txt = c3.text_input("Kredittpåslag (%/år)", value=fmt_float(ux["kredittpaslag_pct"] * 100, 2), key="ux_paslag")
        ebitda_multipel = parse_number(ebitda_mult_txt, ux["ebitda_multipel"])
        swap_rente_pct = parse_number(swap_txt, ux["swap_rente_pct"] * 100) / 100.0
        kredittpaslag_pct = parse_number(paslag_txt, ux["kredittpaslag_pct"] * 100) / 100.0
        bankrente_pct_ar = swap_rente_pct + kredittpaslag_pct
        banklan_belop_preview = kapitalleie_ar * ebitda_multipel
        st.caption(
            f"→ Bankrente: {swap_rente_pct*100:.2f} % swap + {kredittpaslag_pct*100:.2f} % påslag = "
            f"{bankrente_pct_ar*100:.2f} % p.a. Banklån: {fmt_int(kapitalleie_ar)} (EBITDA) x "
            f"{ebitda_multipel:.1f} = {fmt_int(banklan_belop_preview)} kr"
        )
        banklan_ar_val = st.number_input("Nedbetaling banklån (år)", value=int(ux["banklan_nedbetaling_ar"]), min_value=1, key="ux_banklan_ar")
        avskrivningstid_ar = st.number_input("Avskrivningstid anlegg (år, lineært)", min_value=1, max_value=60,
                                             value=int(ux.get("avskrivningstid_ar", 25)), step=1, key="ux_avskrivningstid",
                                             help="Gjelder CAPEX og vedlikeholdsinvesteringer (fra året etter de gjøres). "
                                                  "Påvirker skatt og bokført verdi - ikke EBITDA.")
        c1, c2 = st.columns(2)
        refi_intervall_ar = int(c1.number_input("Refinansier hvert (år)", min_value=0, max_value=20,
                                                value=int(ux.get("refi_intervall_ar", 0)), step=1, key="ux_refi_intervall",
                                                help="0 = ingen refinansiering. Ved refinansiering innfris restgjeld og nytt lån tas opp."))
        refi_mult_txt = c2.text_input("Refinansier til (x neste års EBITDA)", value=fmt_float(ux.get("refi_multipel", 5.0), 1), key="ux_refi_mult")
        refi_multipel = parse_number(refi_mult_txt, ux.get("refi_multipel", 5.0))
        if refi_intervall_ar > 0:
            st.caption(f"→ Hvert {refi_intervall_ar}. år: nytt lån = {refi_multipel:.1f} x neste års EBITDA; "
                       "differansen mot restgjelden utbetales til eier (refinansieringsproveny) og ny annuitet starter.")

        c1, c2, c3 = st.columns(3)
        skattesats_txt = c1.text_input("Skattesats (%)", value=fmt_float(ux["skattesats_pct"] * 100, 1), key="ux_skattesats")
        # Vedlikeholdsinvestering settes som BELØP første år (NOK), deretter
        # inflasjonsjustert - ikke lenger som % av CAPEX (bekreftet av bruker).
        _vedl_default = ux.get("vedlikeholdsinvestering_nok_forste_ar", capex * ux["vedlikeholdsinvestering_pct_capex"])
        vedlikeholdsinvestering_nok = _nok_input("Vedlikeholdsinvestering, første år (NOK)", "ux_vedlikehold_nok", _vedl_default, container=c2)
        vedlikehold_indeks_txt = c3.text_input("- deretter indeksert (%/år)", value=fmt_float(ux["vedlikeholdsinvestering_indeksering_pct_ar"] * 100, 1), key="ux_vedlikehold_indeks")
        skattesats_pct = parse_number(skattesats_txt, ux["skattesats_pct"] * 100) / 100.0
        vedlikeholdsinvestering_pct_capex = (vedlikeholdsinvestering_nok / capex) if capex > 0 else 0.0  # internt format i utleiermodellen
        vedlikeholdsinvestering_indeksering_pct_ar = parse_number(vedlikehold_indeks_txt, ux["vedlikeholdsinvestering_indeksering_pct_ar"] * 100) / 100.0
        st.caption(
            f"→ Vedlikeholdsinvestering første år: {fmt_int(vedlikeholdsinvestering_nok)} kr "
            f"({vedlikeholdsinvestering_pct_capex*100:.2f} % av CAPEX), deretter +{vedlikeholdsinvestering_indeksering_pct_ar*100:.1f} % årlig."
        )

        # Skjult bak en kode, IKKE bare en kollapset boks - se _vis_irr under
        # (i toppen av sidepanelet). Uten riktig kode brukes ux-defaultene
        # stille i bakgrunnen (IRR-tallet beregnes fortsatt internt, kun
        # SELVE VISNINGEN er skjult - se hovedinnholdet lenger ned).
        if _vis_irr:
            st.markdown("**IRR / exit-forutsetninger**")
            c1, c2 = st.columns(2)
            holding_years_val = c1.number_input("Eiertid før exit (år)", value=int(ux["holding_years"]), min_value=1, key="ux_holding_years")
            terminal_mult_txt = c2.text_input("EV/EBITDA-multippel (terminalverdi)", value=fmt_float(ux["terminal_ebitda_multipel"], 1), key="ux_terminal_mult",
                                              help="Terminalverdi ved exit = multippel x EBITDA året ETTER eiertiden (fremadskuende).")
            terminal_ebitda_multipel = parse_number(terminal_mult_txt, ux["terminal_ebitda_multipel"])
            st.caption(
                f"→ IRR beregnes på egenkapitalens kontantstrøm over {holding_years_val} år, med en terminalverdi "
                f"ved utgangen av år {holding_years_val} lik {terminal_ebitda_multipel:.1f}x EBITDA ÅRET ETTER "
                f"(fremadskuende multippel), minus gjenværende saldo på utleiers eget banklån på det tidspunktet."
            )
        else:
            holding_years_val = int(ux["holding_years"])
            terminal_ebitda_multipel = ux["terminal_ebitda_multipel"]

    hexacage_sublinjer = [
        {"id": "leie_131", "navn": "13.1 Kapitalleie"},
        {"id": "leie_132", "navn": "13.2 Oppankring"},
        {"id": "leie_13_teknisk", "navn": "13.3 Teknisk vedlikehold"},
        {"id": "leie_133", "navn": "13.4 Årlig rengjøring innvendig"},
        {"id": "leie_134", "navn": "13.5 Årlig rengjøring av krager"},
        {"id": "leie_135", "navn": "13.6 Desinfeksjon (per kohort)"},
        {"id": "leie_136", "navn": "13.7 Lønn lokalitet (inkl. sosiale kostnader)"},
        {"id": "leie_137", "navn": "13.8 Lønn land (inkl. sosiale kostnader)"},
        {"id": "leie_138", "navn": "13.9 Forsikring"},
        {"id": "leie_139", "navn": "13.10 ADK"},
    ]
    hexacage_sublinjer_ar = {
        "leie_131": kapitalleie_ar, "leie_132": oppankring_ar, "leie_13_teknisk": teknisk_vedlikehold_ar,
        "leie_133": rengj_innv_ar, "leie_134": rengj_krager_ar,
        "leie_135": 0.0,  # desinfeksjon er hendelsesbasert - satt inn separat, se lenger ned
        "leie_136": lonn_lok_ar + sosiale_lok_ar, "leie_137": lonn_land_ar + sosiale_land_ar,
        "leie_138": forsikring_ar, "leie_139": adk_ar,
    }

    fixed_cost_prices["leie_anlegg"] = leie_hexacage_ar / 52.0
    hexacage_sublinjer_kr_per_uke = {k: v / 52.0 for k, v in hexacage_sublinjer_ar.items()}

    # "15. Administrasjonskostnader" har ingen default (tom - fylles inn
    # manuelt når/hvis beløp er kjent). "14. Teknisk vedlikehold" er
    # OPPDRETTERS EGEN konto og skal IKKE ha noen default her - det nye
    # "13.3 Teknisk vedlikehold" (over, del av selve leiebeløpet) er en
    # HELT ANNEN linje enn denne "14."-kontoen, som forblir 0 med vilje.
    #
    # VIKTIG: st.text_input sin `value=`-parameter brukes KUN første gang en
    # widget med denne `key`-en noensinne opprettes i en sesjon - har du
    # allerede interagert med feltet TIDLIGERE i samme (fortsatt kjørende)
    # sesjon, ignoreres `value=` på alle senere reruns, uansett hva som står
    # i koden. Riktig mønster er å så st.session_state direkte FØR widgeten
    # opprettes (kun hvis nøkkelen ikke finnes fra før - overskriver ALDRI
    # noe brukeren selv har skrevet inn).
    _fastkost_default_ar = {k: (v * 52.0 if v else None) for k, v in default_config.FIXED_COST_KR_PER_UKE.items()}
    for fc in default_config.FIXED_COSTS:
        if fc["id"] == "leie_anlegg":
            continue  # dekket av underseksjonen over
        enhet = "kr/uke" if fastkost_periode == "Per uke" else "kr/år"
        default_ar = _fastkost_default_ar.get(fc["id"])
        default_verdi_txt = (
            fmt_int(default_ar / 52.0 if fastkost_periode == "Per uke" else default_ar) if default_ar else ""
        )
        widget_key = f"fastkost_{fc['id']}"
        if widget_key not in st.session_state:
            st.session_state[widget_key] = default_verdi_txt
        def _reformat_fastkost(k=widget_key):
            # Tusenskille på tallet brukeren skriver inn; tomt felt forblir tomt (= 0).
            v = st.session_state[k]
            st.session_state[k] = fmt_int(parse_number(v)) if v.strip() else ""

        txt = st.text_input(f"{fc['navn']} ({enhet})", key=widget_key, on_change=_reformat_fastkost)
        fixed_cost_prices[fc["id"]] = (parse_number(txt) / fastkost_divisor) if txt.strip() else None

    with st.expander("Resultatregnskap - avskrivninger, finans, skatt", expanded=False):
        _rd = getattr(default_config, "RESULTAT_DEFAULTS", {})
        st.caption("Oppdretters EGNE poster under EBITDA. Anlegget eies av utleier (13. Leie er opex), "
                   "så avskrivninger/finans her gjelder kun oppdretters egne eiendeler og lån. "
                   "Skatt beregnes med fremførbart underskudd. Vises foreløpig KUN i Resultatregnskapet "
                   "- ikke bokført i kontantstrøm/balanse ennå.")
        avskrivninger_ar = _nok_input("Avskrivninger (NOK/år)", "res_avskrivninger", _rd.get("avskrivninger_nok_per_ar", 0.0))
        finanskostnader_ar = _nok_input("Finanskostnader (NOK/år)", "res_finans", _rd.get("finanskostnader_nok_per_ar", 0.0))
        skattesats_txt = st.text_input("Skattesats (%)", value=fmt_float(_rd.get("skattesats_pct", 0.22) * 100, 1), key="res_skattesats")
        skattesats = parse_number(skattesats_txt, _rd.get("skattesats_pct", 0.22) * 100) / 100.0

    st.header("Tank")
    # Etiketten følger kakestykke-modus (valgt under Rotasjon): med skyveskott
    # er tallet et SNITTVOLUM per kakestykke (anleggets totale volum / antall),
    # ikke en fast tankstørrelse - veggene flyttes fritt.
    _skott_er_faste = (st.session_state.get("skott_modus") == "Faste skott")
    _n_tank_lbl = int(st.session_state.get("n_tanks", getattr(default_config, "N_TANKS", 6)))
    _vol_lbl = parse_number(str(st.session_state.get("tank_volume_m3", "")), default=float(default_config.TANK_VOLUME_M3))
    if produkttype == "Slaktefisk" and not _skott_er_faste:
        _tank_label = f"Fleksible {_n_tank_lbl} x {fmt_int(_vol_lbl)} m³ - snittvolum per kakestykke (m³)"
    elif produkttype == "Slaktefisk":
        _tank_label = f"Faste skott {_n_tank_lbl} x {fmt_int(_vol_lbl)} m³ - volum per skott (m³)"
    else:
        _tank_label = "Tankvolum (m³)"
    tank_volume_m3 = _auto_format_number_input(
        _tank_label, key="tank_volume_m3", default_value=float(default_config.TANK_VOLUME_M3), min_value=100.0,
    )
    if produkttype == "Slaktefisk":
        st.caption(f"→ Anleggets totale volum: {fmt_int(tank_volume_m3 * _n_tank_lbl)} m³"
                   + (" (fordeles fritt mellom kakestykkene)" if not _skott_er_faste else " (låst per skott)"))
    max_density = st.number_input("Maks tetthet (kg/m³)", value=float(default_config.MAX_DENSITY_KG_M3), step=5.0)

    st.header("Rotasjon")
    multitank = (produkttype == "Slaktefisk")
    if multitank:
        # ---- BIG DIPPER MULTI-TANK (Slaktefisk) ----
        # N parallelle, uavhengige tanker med SAMME oppskrift, innsett
        # forskjøvet et fast antall uker per tank. Oppskrifter-i-rotasjon
        # (sekvensielt i ÉN tank) gir ikke mening her - låst til 1.
        c_t1, c_t2 = st.columns(2)
        n_tanks = int(c_t1.number_input(
            "Antall tanker", min_value=1, max_value=8, value=int(getattr(default_config, "N_TANKS", 6)),
            step=1, key="n_tanks",
            help="Big Dipper har 6 vekstkar à 83 333 m³ (ca. 500 000 m³). Hver tank kjører sin egen, uavhengige "
                 "rotasjon (vekst + vask) med oppskriften under - forskjøvet i tid per tank.",
        ))
        def _bytt_skott_oppsett():
            # Bytter hele oppsettet (antall tanker, tankvolum, smolt per tank)
            # når kakestykke-modus endres - se SKYVESKOTT_/FASTE_SKOTT_DEFAULTS.
            _d = (default_config.FASTE_SKOTT_DEFAULTS if st.session_state.get("skott_modus") == "Faste skott"
                  else default_config.SKYVESKOTT_DEFAULTS)
            st.session_state["n_tanks"] = int(_d["n_tanks"])
            st.session_state["tank_volume_m3"] = fmt_int(_d["tank_volume_m3"])
            for _i, _n in enumerate(_d["smolt"]):
                st.session_state[f"smolt_{_i}"] = fmt_int(_n)
        skott_modus = st.radio(
            "Kakestykker", options=["Skyveskott (fleksible vegger)", "Faste skott"], index=0, key="skott_modus",
            on_change=_bytt_skott_oppsett,
            help="'Skyveskott': veggene flyttes, hver kohort får det volumet biomassen krever ved tetthetstaket, "
                 "og kravet er at SUMMEN over alle kohorter holder seg under anleggets totale volum. "
                 "'Faste skott': hvert kakestykke er låst til 'Tankvolum', og HVER kohort må holde seg under "
                 "tetthetstaket i sitt eget skott hele veien. Bytte setter automatisk hele oppsettet: "
                 "skyveskott = 6 x 83 333 m³ / 850 000 fisk; faste skott = 8 x 62 500 m³ med kalibrert "
                 "smoltantall per tank (399 000-452 000).",
        )
        skott_faste = (skott_modus == "Faste skott")
        if skott_faste:
            _peak = st.session_state.get("_peak_density_per_tank")
            _cap = float(st.session_state.get("max_density_kg_m3", default_config.MAX_DENSITY_KG_M3))
            def _kalibrer_smolt(peak=_peak, cap=_cap):
                # Tetthet er lineær i antall fisk -> nytt antall = dagens x tak / topp (1 % margin)
                if not peak:
                    return
                for _i, (_n, _pk) in enumerate(peak):
                    if _pk and _pk > 0:
                        st.session_state[f"smolt_{_i}"] = fmt_int(int(_n * cap / _pk * 0.99 // 1000 * 1000))
            st.button("Kalibrer smoltantall per tank til tetthetstaket", key="kalibrer_smolt_knapp",
                      on_click=_kalibrer_smolt, disabled=not _peak,
                      help="Setter antall fisk i hver tank til det høyeste som holder maks tetthet under taket "
                           "(1 % margin) - basert på forrige kjøring, så trykk igjen om du endrer noe annet.")
        c_y, c_m = st.columns(2)
        start_year = int(c_y.number_input("Startår", min_value=2024, max_value=2040,
                                          value=int(default_config.START_ISO_YEAR), step=1, key="startaar"))
        innsett_monster = c_m.radio(
            "Innsettmønster", options=["Jevnt fordelt over året", "Fast antall uker"], index=0, key="innsett_monster",
            help="'Annenhver måned': tank 1 settes inn første hele uke i januar, tank 2 i mars, tank 3 i mai, "
                 "osv. (jan/mar/mai/jul/sep/nov) - rett på månedsgrensene, så kakediagrammene blir lette å "
                 "lese. 'Fast antall uker': tank 2 settes inn N uker etter tank 1, tank 3 N uker etter tank 2.",
        )
        if innsett_monster == "Jevnt fordelt over året":
            _d0 = date(start_year, 1, 1)
            _d0 = _d0 + timedelta(days=(7 - _d0.weekday()) % 7)   # første mandag i januar
            _iso0 = _d0.isocalendar()
            start_week, start_year = int(_iso0[1]), int(_iso0[0])
            if 12 % max(1, n_tanks) == 0:
                # Går opp i 12: første MANDAG i hver n-te måned (6 tanker = jan, mar,
                # mai, jul, sep, nov) - rene månedsgrenser i kakediagrammene.
                _mnd_steg = 12 // n_tanks
                _innsett_datoer = []
                for t in range(n_tanks):
                    _d = date(start_year, 1 + t * _mnd_steg, 1)
                    _innsett_datoer.append(_d + timedelta(days=(7 - _d.weekday()) % 7))
                _anker = monday_of_week(0, start_year, start_week)
                tank_start_offsets = [(d - _anker).days // 7 for d in _innsett_datoer]
            else:
                # Går ikke opp i 12 (f.eks. 8 tanker): jevnt i uker, 52/n mellom
                # hvert innsett (8 tanker = uke 0, 7, 13, 20, 26, 33, 39, 46).
                tank_start_offsets = [round(t * 52 / n_tanks) for t in range(n_tanks)]
            tank_stagger = tank_start_offsets[1] - tank_start_offsets[0] if n_tanks > 1 else 0
        else:
            c_w, c_s = st.columns(2)
            start_week = int(c_w.number_input("Startuke (tank 1, ISO-uke)", min_value=1, max_value=53,
                                              value=int(default_config.START_ISO_WEEK), step=1, key="startuke"))
            tank_stagger = int(c_s.number_input(
                "Uker mellom innsett", min_value=0, max_value=52,
                value=int(getattr(default_config, "TANK_STAGGER_WEEKS", 8)), step=1, key="tank_stagger",
            ))
            tank_start_offsets = [t * tank_stagger for t in range(n_tanks)]
        n_batches = n_tanks  # ÉN oppskrift PER TANK (tank 1 = oppskrift 1, osv.)
        oppskrift1_dato = monday_of_week(0, start_year, start_week)
        _tank_uker = ", ".join(
            f"T{t + 1}: {week_label(tank_start_offsets[t], start_year, start_week)[0]}" for t in range(n_tanks)
        )
        st.caption(f"Første innsett per tank → {_tank_uker}")
        st.caption("Én oppskrift PER TANK (smoltvekt, antall, vekst-/vaskeuker, salgsvindu) - tankene "
                   "settes inn i ulike sesonger og kan derfor trenge ulik fisk/antall. Neste kohort i "
                   "SAMME tank gjentar tankens oppskrift rett etter vekst + vask (med 50 + 1 uker glir "
                   "innsettuken 1 uke tidligere per år).")
    else:
        n_tanks, tank_stagger, tank_start_offsets, skott_faste = 1, 0, [0], False
        n_batches = st.number_input("Antall oppskrifter i rotasjon", min_value=1, max_value=8,
                                     value=int(default_config.N_BATCHES_IN_ROTATION), step=1, key="n_batches")

        default_start_dato = date(int(default_config.START_ISO_YEAR), 1, 1) + timedelta(weeks=int(default_config.START_ISO_WEEK) - 1)
        oppskrift1_dato = st.date_input(
            "Oppskrift 1 sin startdato (styrer hele rotasjonen)", value=default_start_dato, key="oppskrift1_dato",
            format="DD.MM.YYYY",
            help="Ankeret for HELE rotasjonen - Oppskrift 2 (og alle senere oppskrifter/kohorter) "
                 "følger automatisk rett etter, siden det kun er ÉN tank (se datoene vist under hver "
                 "oppskrift etter at du har satt vekst-/vaskeuker).",
        )
        _iso = oppskrift1_dato.isocalendar()
        start_year, start_week = _iso[0], _iso[1]
        st.caption(f"→ ISO-uke {start_week}, {start_year} (mandag {monday_of_week(0, start_year, start_week).strftime('%d.%m.%Y')})")

    default_start_weights_g = [w * 1000.0 for w in getattr(default_config, "BATCH_START_WEIGHT_KG", [default_config.START_WEIGHT_KG])]

    if not multitank:
        st.caption("Én rad per oppskrift - juster fritt. Smoltvekt kan nå settes ULIKT per oppskrift "
                   "(f.eks. to kohorter i året med forskjellig innsettvekt) - prisen på kjøpt smolt "
                   "(formelbasert, se 'Ressursregnskap' under) følger automatisk DENNE kohortens egen vekt.")
    smolt_counts, growth_weeks, cleaning_weeks, sales_window_weeks, start_weights_g = [], [], [], [], []
    ekstra_venteuker = [0] * int(n_batches)
    oppskrift_resultat_placeholder = []  # fylles inn LENGER NED i skriptet, når leveringsvekt faktisk er kjent
    uke_offset = 0  # kumulativ vekst+vask+ekstra venteuker - brukes til å vise/sette NESTE oppskrifts startdato
    for i in range(int(n_batches)):
        if multitank:
            _t_uke, _t_dato = week_label(tank_start_offsets[i], start_year, start_week)
            st.markdown(f"**Tank {i + 1} - oppskrift** (første innsett {_t_uke}, {_t_dato.strftime('%d.%m.%Y')})")
        else:
            st.markdown(f"**Oppskrift {i + 1}**")
        oppskrift_resultat_placeholder.append(st.empty())
        if multitank:
            pass  # startuke per tank er vist i overskriften
        elif i == 0:
            st.caption(f"Starter {oppskrift1_dato.strftime('%d.%m.%Y')} (satt over).")
        else:
            naturlig_dato = oppskrift1_dato + timedelta(weeks=uke_offset)
            valgt_dato = st.date_input(
                f"Oppskrift {i+1} sin startdato", value=naturlig_dato, key=f"oppskrift_dato_{i}",
                format="DD.MM.YYYY",
                help=f"Tidligst mulig er {naturlig_dato.strftime('%d.%m.%Y')} (rett etter Oppskrift {i} "
                     f"sin vekst + vask - kan IKKE settes tidligere, det er kun én tank). En SENERE "
                     f"dato gir tanken en ekstra ledig (tom) periode før denne oppskriften starter - "
                     f"gjentas hver gang denne oppskriften kommer opp igjen i rotasjonen (ikke bare "
                     f"første gang).",
            )
            ekstra_uker_i = (valgt_dato - naturlig_dato).days // 7
            if ekstra_uker_i < 0:
                st.warning(
                    f"Oppskrift {i+1} kan tidligst starte {naturlig_dato.strftime('%d.%m.%Y')} - én "
                    f"tank kan ikke romme to kohorter samtidig. Bruker denne datoen i stedet."
                )
                ekstra_uker_i = 0
            ekstra_venteuker[i] = ekstra_uker_i
            if ekstra_uker_i > 0:
                st.caption(f"→ {ekstra_uker_i} ekstra ledig(e) uke(r) i tanken før denne oppskriften.")
        default_smolt = default_config.BATCH_SMOLT_COUNTS[i] if i < len(default_config.BATCH_SMOLT_COUNTS) else default_config.BATCH_SMOLT_COUNTS[-1]
        default_gw = default_config.BATCH_GROWTH_WEEKS[i] if i < len(default_config.BATCH_GROWTH_WEEKS) else default_config.BATCH_GROWTH_WEEKS[-1]
        default_cw = default_config.BATCH_CLEANING_WEEKS[i] if i < len(default_config.BATCH_CLEANING_WEEKS) else default_config.BATCH_CLEANING_WEEKS[-1]
        default_sw = (default_config.BATCH_SALES_WINDOW_WEEKS[i]
                       if i < len(default_config.BATCH_SALES_WINDOW_WEEKS) else default_config.BATCH_SALES_WINDOW_WEEKS[-1])
        default_startw = default_start_weights_g[i] if i < len(default_start_weights_g) else default_start_weights_g[-1]
        c0, c1, c2, c3, c4 = st.columns(5)
        start_weights_g.append(float(c0.number_input(
            f"Smoltvekt #{i+1} (g)", value=float(default_startw), min_value=1.0, step=5.0, key=f"startw_{i}",
        )))
        smolt_counts.append(int(_auto_format_number_input(
            f"Smolt #{i+1}", key=f"smolt_{i}", default_value=float(default_smolt),
            min_value=0.0, container=c1,
        )))
        growth_weeks.append(int(c2.number_input(f"Vekst-uker #{i+1}", value=default_gw, min_value=1, key=f"gw_{i}")))
        cleaning_weeks.append(int(c3.number_input(f"Vask-uker #{i+1}", value=default_cw, min_value=0, key=f"cw_{i}")))
        sales_window_weeks.append(int(c4.number_input(
            f"Salgsvindu-uker #{i+1}", value=default_sw, min_value=1, max_value=growth_weeks[i], key=f"sw_{i}",
            help="Antall uker kohorten SELGES over. 1 = alt levert i én uke (som før). Ved f.eks. "
                 "4: de fire SISTE vekstukene blir egne batcher, solgt med avtakende andel av "
                 "gjenværende bestand (1/4, 1/3, 1/2, 1/1) - gir jevnstore batcher.",
        )))
        uke_offset += growth_weeks[i] + cleaning_weeks[i] + ekstra_venteuker[i]
    start_weight_g = start_weights_g[0]  # brukes noen få steder i appen som "den" smoltvekten (visning/sammendrag) -
                                          # se cfg.START_WEIGHT_KG under, som settes til FØRSTE oppskrifts vekt.

    st.header("Dødelighet / vekst")
    annual_mortality = st.number_input("Dødelighet (%/år)", value=float(default_config.ANNUAL_MORTALITY_PCT), step=0.1)
    rgi_pct = st.number_input("RGI (%)", value=float(default_config.RGI_PCT), step=1.0, key="rgi_pct")

    _maned_navn = ["Jan", "Feb", "Mar", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Des"]
    _maned_nokkel = [f"temp_maned_{i}" for i in range(12)]

    def _bruk_temperaturprofil():
        valgt_profil = st.session_state["temperaturprofil_valg"]
        for nokkel, verdi in zip(_maned_nokkel, default_config.TEMPERATURE_PROFILES[valgt_profil]):
            st.session_state[nokkel] = float(verdi)

    temperaturprofil_valg = st.radio(
        "Sjøtemperatur - profil (utgangspunkt - alle måneder er redigerbare under)",
        options=list(default_config.TEMPERATURE_PROFILES.keys()),
        index=list(default_config.TEMPERATURE_PROFILES.keys()).index(default_config.DEFAULT_TEMPERATURE_PROFILE),
        key="temperaturprofil_valg", on_change=_bruk_temperaturprofil,
        help="'25 m under overflaten (lukket anlegg)': dempet sesongsvingning, kaldeste måned 5,5°C, "
             "varmeste 12,0°C. 'Konvensjonell dybde': noe varmere om sommeren (opp til 15,8°C i "
             "september), ellers tilsvarende om vinteren. Fyller inn alle 12 månedsfelt under når "
             "du bytter - juster hvert enkelt fritt etterpå.",
    )

    st.caption("Månedlig sjøtemperatur (°C)")
    _default_profil = default_config.TEMPERATURE_PROFILES[default_config.DEFAULT_TEMPERATURE_PROFILE]
    manedlig_temp_c = []
    for rad_start in range(0, 12, 4):
        kolonner = st.columns(4)
        for i, kol in zip(range(rad_start, rad_start + 4), kolonner):
            verdi = kol.number_input(
                _maned_navn[i], value=float(_default_profil[i]), step=0.1, format="%.1f", key=_maned_nokkel[i],
            )
            manedlig_temp_c.append(verdi)

    st.header("Kalender")
    n_years = st.number_input("Antall år å simulere", value=int(default_config.N_YEARS_TO_RUN), min_value=1, max_value=15)

    st.header("Ressursregnskap - enheter og priser")
    st.caption(
        "For hver ressurslinje: enhet/faktor (hvor mye som går med per kg WFE, "
        "der det gjelder) og pris. La prisfeltet stå tomt for å vise kun mengde."
    )
    resource_factors, resource_prices = {}, {}
    for r in default_config.RESOURCES:
        st.markdown(f"**{r['navn']}**")
        default_price = default_config.RESOURCE_PRICES_NOK.get(r["id"])
        if default_price is None:
            default_price_str = ""
        elif default_price == int(default_price):
            default_price_str = str(int(default_price))
        else:
            default_price_str = str(default_price)
        if r["kilde"] == "smolt":
            st.caption("Enhet: 1 stk per kjøpt smolt")
            smolt_pris_modus = st.radio(
                "Smoltpris-modell", options=["Formel (fastdel + sats × vekt)", "Fiskeverditabell (interpolert)"],
                index=0 if default_config.SMOLT_PRIS_MODUS == "Formel" else 1,
                key="smolt_pris_modus",
                help="'Formel': fastdel (kr) + sats (kr/gram) x smoltvekt, som før. 'Fiskeverditabell': "
                     "kr/kg fra en fast tabell (60 g-1000 g), interpolert lineært mellom punktene til "
                     "DEN spesifikke kohortens/oppskriftens egen smoltvekt. Vekt over 1000 g bruker "
                     "1000 g sin sats (ingen ekstrapolering utenfor tabellen). Salgsprisen på FERDIG "
                     "levert/slaktet fisk er UPÅVIRKET av dette valget - kun kostnaden ved KJØP av "
                     "smolt (0. Kjøpt smolt) styres av denne bryteren.",
            )
            cfg_smolt_pris_modus = "Formel" if smolt_pris_modus.startswith("Formel") else "Tabell"
            # Default-verdier FØR forgreningen under, slik at BEGGE variablene
            # alltid finnes uansett hvilken modus som er valgt - de trengs
            # begge lenger ned i skriptet for å settes på selve cfg-objektet
            # (se cfg.SMOLT_PRICE_BASE_KR = ... nedenfor).
            smolt_price_base = float(default_config.SMOLT_PRICE_BASE_KR)
            smolt_price_per_gram = float(default_config.SMOLT_PRICE_PER_GRAM_KR)

            if cfg_smolt_pris_modus == "Formel":
                st.caption("Formel: fastdel (kr) + sats (kr/gram) x smoltvekt")
                fc, gc = st.columns(2)
                smolt_price_base = fc.number_input(
                    "Fastdel (kr)", value=float(default_config.SMOLT_PRICE_BASE_KR),
                    step=1.0, key="smolt_price_base",
                )
                smolt_price_per_gram = gc.number_input(
                    "Sats (kr/gram)", value=float(default_config.SMOLT_PRICE_PER_GRAM_KR),
                    step=0.01, format="%.2f", key="smolt_price_per_gram",
                )
                computed_smolt_price = smolt_price_base + smolt_price_per_gram * start_weight_g
                if len(start_weights_g) > 1:
                    pris_per_oppskrift = ", ".join(
                        f"#{i+1}: {fmt_float(smolt_price_base + smolt_price_per_gram * w, 2)} kr/stk "
                        f"({fmt_float(w,0)} g, = {fmt_float((smolt_price_base + smolt_price_per_gram * w) / (w/1000.0), 2)} kr/kg)"
                        for i, w in enumerate(start_weights_g)
                    )
                    st.caption(f"Beregnet pris per oppskrift (følger DEN oppskriftens egen vekt): {pris_per_oppskrift}")
                else:
                    implisitt_pris_kr_per_kg = computed_smolt_price / (start_weight_g / 1000.0) if start_weight_g else 0.0
                    st.caption(f"Beregnet pris ved {fmt_float(start_weight_g, 0)} g: "
                               f"{fmt_float(computed_smolt_price, 2)} kr/stk")
                    st.caption(f"→ Implisitt pris per kg kjøpt smolt: {fmt_float(implisitt_pris_kr_per_kg, 2)} kr/kg")
                price_txt = str(computed_smolt_price)
            else:
                st.caption(
                    "Kr/kg fra fiskeverditabellen (60 g-1000 g), interpolert til denne kohortens "
                    "egen smoltvekt. Rediger tabellen i config_1tank.py (SMOLT_VERDITABELL_KR_PER_KG) "
                    "hvis prisene endrer seg."
                )
                # VIKTIG: beregnes og vises FØR den kollapsede "Vis fiskeverditabellen"-boksen under,
                # slik at "hva har vi faktisk betalt" er synlig med det samme - ikke skjult bak en
                # boks brukeren må åpne selv.
                if len(start_weights_g) > 1:
                    for i, w in enumerate(start_weights_g):
                        kr_per_kg = interpoler_fiskeverdi_kr_per_kg(w, default_config.SMOLT_VERDITABELL_KR_PER_KG)
                        pris_stk = kr_per_kg * (w / 1000.0)
                        st.markdown(f"**Oppskrift {i+1}** ({fmt_float(w,0)} g): {fmt_float(pris_stk,2)} kr/stk = {fmt_float(kr_per_kg,2)} kr/kg")
                    computed_smolt_price = interpoler_fiskeverdi_kr_per_kg(
                        start_weight_g, default_config.SMOLT_VERDITABELL_KR_PER_KG
                    ) * (start_weight_g / 1000.0)
                else:
                    kr_per_kg = interpoler_fiskeverdi_kr_per_kg(start_weight_g, default_config.SMOLT_VERDITABELL_KR_PER_KG)
                    computed_smolt_price = kr_per_kg * (start_weight_g / 1000.0)
                    st.markdown(f"**Betalt for denne kohorten**: {fmt_float(start_weight_g, 0)} g -> "
                                f"{fmt_float(computed_smolt_price, 2)} kr/stk = {fmt_float(kr_per_kg, 2)} kr/kg")
                with st.expander("Vis fiskeverditabellen", expanded=False):
                    _tabell_df = pd.DataFrame(
                        default_config.SMOLT_VERDITABELL_KR_PER_KG, columns=["Vekt (g)", "Kr/kg"]
                    )
                    _tabell_df["Pris per stk (kr)"] = (
                        (_tabell_df["Vekt (g)"] / 1000.0) * _tabell_df["Kr/kg"]
                    ).round(2)
                    st.dataframe(_tabell_df, hide_index=True, use_container_width=True)
                    st.caption(
                        "Vekt UNDER 60 g bruker 60 g sin sats; vekt OVER 1000 g bruker 1000 g sin "
                        "sats (ingen ekstrapolering utenfor tabellen). Mellom punktene interpoleres "
                        "det lineært. 'Pris per stk' = vekt (kg) x kr/kg, ved akkurat DEN tabellvekten."
                    )
                price_txt = str(computed_smolt_price)
        elif r["kilde"] == "feed":
            st.caption("Enhet: kg - beregnes automatisk fra Skretting-fôrmodellen")
            price_txt = st.text_input("Pris (kr/kg)", value=default_price_str, key=f"price_{r['id']}")
        elif r["id"] == "andre_produksjon":
            st.caption("Enhet: kg WFE - forsikring av biomasse, priset som en andel av salgsprisen")
            st.caption("Formel: forsikringssats x (andel av salgspris x salgspris)")
            bf = default_config.BIOMASSEFORSIKRING_DEFAULTS
            fc, gc = st.columns(2)
            biomasse_andel_pct = fc.number_input(
                "Andel av salgspris (%)", value=float(bf["andel_av_salgspris_pct"] * 100),
                step=1.0, format="%.1f", key="biomasse_andel_pct",
            ) / 100.0
            biomasse_forsikringssats_pct = gc.number_input(
                "Forsikringssats (%)", value=float(bf["forsikringssats_pct"] * 100),
                step=0.1, format="%.2f", key="biomasse_forsikringssats_pct",
            ) / 100.0
            salgspris_forelopig = parse_number(salgspris_txt, 100.0) if salgspris_txt.strip() else 100.0
            biomasse_verdi_per_kg = salgspris_forelopig * biomasse_andel_pct
            computed_biomasse_forsikring = biomasse_forsikringssats_pct * biomasse_verdi_per_kg
            st.caption(
                f"Forsikret verdi: {fmt_float(salgspris_forelopig, 0)} x {biomasse_andel_pct*100:.0f} % = "
                f"{fmt_float(biomasse_verdi_per_kg, 2)} kr/kg WFE → Forsikring: "
                f"{biomasse_forsikringssats_pct*100:.2f} % x {fmt_float(biomasse_verdi_per_kg, 2)} = "
                f"{fmt_float(computed_biomasse_forsikring, 2)} kr/kg WFE"
            )
            resource_factors[r["id"]] = r.get("faktor_per_kg_wfe", 1.0)
            price_txt = str(computed_biomasse_forsikring)
        elif r["id"] == "energi":
            # 2. MGO: energibehov (kWh/kg WFE) -> liter diesel via energiinnhold
            # og generator-virkningsgrad. Faktoren (liter/kg WFE) regnes ut her.
            st.caption("Enhet: liter MGO - energibehov i kWh per kg WFE, omregnet til diesel")
            m1, m2, m3 = st.columns(3)
            mgo_kwh_per_kg = m1.number_input("kWh per kg WFE", value=float(getattr(default_config, "MGO_KWH_PER_KG_WFE", 1.0)),
                                             step=0.1, format="%.2f", key="mgo_kwh_per_kg",
                                             help="1 MWh per tonn WFE produsert = 1,0 kWh/kg.")
            mgo_kwh_per_l = m2.number_input("kWh per liter MGO", value=float(getattr(default_config, "MGO_KWH_PER_LITER", 10.2)),
                                            step=0.1, format="%.1f", key="mgo_kwh_per_liter",
                                            help="Energiinnhold i drivstoffet (ca. 10,2 kWh/l).")
            mgo_eta = m3.number_input("Virkningsgrad (%)", value=float(getattr(default_config, "MGO_VIRKNINGSGRAD", 0.40) * 100),
                                      step=1.0, format="%.0f", key="mgo_virkningsgrad",
                                      help="Dieselgenerator: andel av drivstoffenergien som blir til levert energi (typisk 38-42 %).") / 100.0
            mgo_l_per_kwh = 1.0 / (mgo_kwh_per_l * mgo_eta) if mgo_kwh_per_l * mgo_eta > 0 else 0.0
            resource_factors[r["id"]] = mgo_kwh_per_kg * mgo_l_per_kwh
            st.caption(
                f"→ {mgo_l_per_kwh:.3f} liter per kWh → {resource_factors[r['id']]:.3f} liter MGO per kg WFE "
                f"({resource_factors[r['id']] * 1000:.0f} liter per tonn WFE)"
            )
            price_txt = st.text_input("Pris (kr/liter MGO)", value=default_price_str, key=f"price_{r['id']}")
        else:  # kilde == "wfe"
            fc, pc = st.columns(2)
            factor_default = r.get("faktor_per_kg_wfe", 1.0)
            resource_factors[r["id"]] = fc.number_input(
                f"Enhet ({r['enhet']}/kg WFE)", value=float(factor_default),
                step=0.01, format="%.2f", key=f"factor_{r['id']}",
            )
            price_txt = pc.text_input(f"Pris (kr/{r['enhet']})", value=default_price_str, key=f"price_{r['id']}")
        resource_prices[r["id"]] = parse_number(price_txt) if price_txt.strip() else None

    st.header("Årlig indeksering")
    st.caption(
        "Eskalerer inntekt OG alle kostnadslinjer (0-12, 13.1-13.10, 14, 15, 16) ÅRLIG, fast fra og "
        "med 1. januar hvert år (ikke en jevn opptrapping gjennom året - prisen hopper opp ved "
        "hvert årsskifte). Rediger satsen PER ÅR direkte i tabellen under - så du f.eks. kan "
        "sette 3 % akkurat i 2029 i stedet for standard 2 %, hvis du vet det kommer en økning "
        "der. Default er 2 % for alt (inkl. 13.1 Kapitalleie), 3 % for det som gjelder ansatte "
        "(lønn, direkte/indirekte, inkl. 13.6/13.7). '13.2 Oppankring' er IKKE med - det er et "
        "låneavdrag med fast nedbetalingsplan. Tabellen 'Årlig indeksering' nederst på siden er "
        "OUTPUT av satsene du setter her - de nominelle kronebeløpene som faktisk går inn i "
        "kontantstrømmen."
    )
    eskalering_linjer = (
        [("inntekt", "Inntekt (salgspris)")]
        + [(r["id"], r["navn"]) for r in default_config.RESOURCES]
        + [(sl["id"], sl["navn"]) for sl in hexacage_sublinjer if sl["id"] != "leie_132"]
        + [(fc["id"], fc["navn"]) for fc in default_config.FIXED_COSTS if fc["id"] != "leie_anlegg"]
    )
    lonn_ider = {"annet_direkte_lonn", "indirekte_lonn", "leie_136", "leie_137"}
    ingen_eskalering_ider = set()  # 13.1 Kapitalleie eskalerer nå også som default (2 %, se _default_sats())

    def _default_sats(id_: str) -> float:
        if id_ in ingen_eskalering_ider:
            return 0.0
        if id_ in lonn_ider:
            return 3.0
        return 2.0

    eskalering_ar_input = list(range(int(start_year), int(start_year) + int(n_years) + 2))
    eskalering_ar_kolonner = [str(y) for y in eskalering_ar_input[1:]]  # basisåret har ingen sats (år 1 = 100%)

    eskalering_default_df = pd.DataFrame({
        "Linje": [navn for _, navn in eskalering_linjer],
        **{
            kol: [_default_sats(id_) for id_, _ in eskalering_linjer]
            for kol in eskalering_ar_kolonner
        },
    })
    st.caption(
        f"Satser i % - én kolonne per år fra {eskalering_ar_kolonner[0]} (basisåret {start_year} har ingen sats). "
        "13.1 Kapitalleie er nå satt til 2 % default (samme som de fleste andre linjer) - endre "
        "fritt i tabellen hvis dere ønsker en annen sats, eller 0 % for et fast NOK-beløp år for år."
    )
    eskalering_edited = st.data_editor(
        eskalering_default_df, hide_index=True, use_container_width=True,
        disabled=["Linje"], key="eskalering_editor",
    )
    escalation_rates_by_year = {
        id_: {int(kol): float(eskalering_edited.loc[i, kol]) / 100.0 for kol in eskalering_ar_kolonner}
        for i, (id_, _) in enumerate(eskalering_linjer)
    }

    # Årlige basisbeløp for de faste kostnadslinjene (13.1-13.9 unntatt
    # 13.2, samt 14/15) - brukt av indekseringstabellen til visning lenger
    # ned. Selve beregningen (build_fixed_costs_weekly) bruker satsene over
    # direkte, uavhengig av denne visningslisten.
    eskalering_ekstra_linjer = [
        {"id": sl["id"], "navn": sl["navn"], "base_verdi": hexacage_sublinjer_ar[sl["id"]]}
        for sl in hexacage_sublinjer if sl["id"] != "leie_132"
    ]
    for fc in default_config.FIXED_COSTS:
        if fc["id"] == "leie_anlegg":
            continue
        ukentlig = fixed_cost_prices.get(fc["id"])
        eskalering_ekstra_linjer.append({
            "id": fc["id"], "navn": fc["navn"],
            "base_verdi": (ukentlig * 52 if ukentlig is not None else None),
        })

# ----------------------------------------------------------------------
# BYGG CONFIG-OBJEKT FOR DENNE KJØRINGEN
# ----------------------------------------------------------------------
class RunConfig:
    pass


cfg = RunConfig()
cfg.TANK_VOLUME_M3 = tank_volume_m3
cfg.MAX_DENSITY_KG_M3 = max_density
cfg.N_BATCHES_IN_ROTATION = int(n_batches)
cfg.START_WEIGHT_KG = start_weight_g / 1000.0
cfg.BATCH_START_WEIGHT_KG = [w / 1000.0 for w in start_weights_g]
cfg.BATCH_SMOLT_COUNTS = smolt_counts
# VIKTIG (rettet feil): disse ble tidligere ALDRI satt på selve cfg-
# objektet (kun default_config, modulens statiske startverdi, hadde dem) -
# det gjorde at build_resource_ledger() sin per-kohort formelbaserte
# smoltprising (hasattr(cfg, "SMOLT_PRICE_BASE_KR")) aldri faktisk slo inn
# i den virkelige appen, uansett hva brukeren skrev inn i sidepanelet.
cfg.SMOLT_PRICE_BASE_KR = smolt_price_base
cfg.SMOLT_PRICE_PER_GRAM_KR = smolt_price_per_gram
cfg.SMOLT_PRIS_MODUS = cfg_smolt_pris_modus
cfg.SMOLT_VERDITABELL_KR_PER_KG = default_config.SMOLT_VERDITABELL_KR_PER_KG
cfg.BATCH_GROWTH_WEEKS = growth_weeks
cfg.BATCH_CLEANING_WEEKS = cleaning_weeks
cfg.BATCH_SALES_WINDOW_WEEKS = sales_window_weeks
cfg.BATCH_EKSTRA_VENTEUKER = ekstra_venteuker
cfg.ANNUAL_MORTALITY_PCT = annual_mortality
cfg.RGI_PCT = rgi_pct
cfg.MONTHLY_TEMPERATURES_C = manedlig_temp_c
cfg.START_ISO_YEAR = int(start_year)
cfg.START_ISO_WEEK = int(start_week)
cfg.N_YEARS_TO_RUN = int(n_years)
cfg.AVSKRIVNINGER_KR_PER_AR = avskrivninger_ar
cfg.FINANSKOSTNADER_KR_PER_AR = finanskostnader_ar
cfg.SKATTESATS = skattesats
cfg.N_TANKS = int(n_tanks)                 # Big Dipper multi-tank (Slaktefisk); 1 for Postsmolt
cfg.TANK_STAGGER_WEEKS = int(tank_stagger)
cfg.TANK_START_WEEK_OFFSETS = list(tank_start_offsets)   # globale ukeindekser for første innsett per tank
cfg.SKOTT_FASTE = bool(skott_faste)   # True = faste kakestykker (tetthetstak per tank), False = skyveskott (anleggsnivå)
cfg.RESOURCES = [
    {**r, "faktor_per_kg_wfe": resource_factors[r["id"]]} if r["id"] in resource_factors else dict(r)
    for r in default_config.RESOURCES
]
if produkttype == "Postsmolt":
    # Postsmolt har verken slaktekostnad eller distribusjonskostnad -
    # nullstiller faktoren (ikke bare prisen) slik at mengden også vises
    # som 0, uansett hva som står i sidepanelets faktor-/prisfelt for disse.
    for r in cfg.RESOURCES:
        if r["id"] in ("slakt", "distribusjon"):
            r["faktor_per_kg_wfe"] = 0.0
cfg.RESOURCE_PRICES_NOK = resource_prices
cfg.WFE_FAKTOR = default_config.WFE_FAKTOR
cfg.SALES_PRICE_KR_PER_KG = parse_number(salgspris_txt) if salgspris_txt.strip() else 0.0
cfg.SALGSPRIS_MODUS = salgspris_modus
_sales_price_table_runtime = (
    default_config.SMOLT_VERDITABELL_KR_PER_KG if salgspris_modus == "Følger fiskeverditabellen" else None
)
cfg.USE_SEASONAL_PRICE_INDEX = bruk_sesongpris
cfg.ESCALATION_RATES_BY_YEAR = escalation_rates_by_year
cfg.ESCALATION_BASE_YEAR = int(start_year)
cfg.PRODUKTTYPE = produkttype
cfg.HOG_FAKTOR = hog_faktor
cfg.FIXED_COSTS = default_config.FIXED_COSTS
cfg.FIXED_COST_KR_PER_UKE = fixed_cost_prices
cfg.HEXACAGE_LEIE_SUBLINJER = hexacage_sublinjer
cfg.HEXACAGE_LEIE_SUBLINJER_KR_PER_UKE = hexacage_sublinjer_kr_per_uke


@st.cache_resource
def _load_growth_tables():
    return GrowthTables(fcr_csv="data/fcr_table.csv", sgr_csv="data/sgr_table.csv")


gt = _load_growth_tables()
if multitank:
    # N parallelle tanker (Big Dipper) - lag oppå den urørte 1-tank-motoren,
    # se scheduler_multitank.py. Med N_TANKS = 1 er dette identisk med
    # build_1tank_schedule (samme ID-er, samme tall).
    weekly_df, generations, cohorts, meta = build_multitank_schedule(cfg, growth_tables=gt)
else:
    weekly_df, generations, cohorts, meta = build_1tank_schedule(cfg, growth_tables=gt)
# Etikett for grupperingsdimensjonen info["batch"]: "Oppskrift N" i 1-tank-
# rotasjon, "Tank N" når flere tanker kjører parallelt.
n_tanker_aktiv = int(meta.get("n_tanks", 1))
gruppe_navn = "Tank" if n_tanker_aktiv > 1 else "Oppskrift"
ledger = build_resource_ledger(cfg, cohorts, generations)
by_cohort = summarize_by_cohort(ledger, cfg)
by_month = summarize_by_month(ledger, cfg)
by_year = summarize_by_year(ledger, cfg)

complete_gens = generations  # alle kohorter her er allerede fullt simulert

# Fyller nå inn plassholderne fra "Rotasjon"-seksjonen i sidepanelet (se
# over) med den FAKTISKE leveringsvekten og pris per fisk for hver
# oppskrift - basert på FØRSTE kohort som bruker hver oppskrift. Kan ikke
# vises der oppe, siden det krever hele vekstsimuleringen (RGI, sesong,
# temperaturprofil osv.), som ikke er kjørt før nå.
_solgt_enhet_preview = "HOG" if cfg.PRODUKTTYPE == "Slaktefisk" else "WFE"
for _i, _placeholder in enumerate(oppskrift_resultat_placeholder):
    _forste_kohort = next((info for info in generations.values() if info["batch"] == _i + 1), None)
    if _forste_kohort is None:
        continue
    _vekt_g = _forste_kohort["delivery_weight_kg"] * 1000
    _solgt_vekt_kg = _forste_kohort["delivery_weight_kg"] * cfg.HOG_FAKTOR
    _pris_stk = _solgt_vekt_kg * cfg.SALES_PRICE_KR_PER_KG if cfg.SALES_PRICE_KR_PER_KG else None
    _tekst = f"→ Leveringsvekt: **{fmt_float(_vekt_g, 0)} g**"
    if _pris_stk is not None:
        _tekst += f" → **{fmt_float(_pris_stk, 2)} kr/stk** (à {fmt_float(cfg.SALES_PRICE_KR_PER_KG, 0)} kr/kg {_solgt_enhet_preview})"
    _placeholder.caption(_tekst)

# ----------------------------------------------------------------------
# METRICS
# ----------------------------------------------------------------------
col1, col2, col3 = st.columns(3)
if n_tanker_aktiv > 1:
    col1.metric("Tanker (parallelle)", f"{n_tanker_aktiv}")
    _rot = sorted(set(meta.get("rotasjon_per_tank", [meta["full_rotasjon_uker"]])))
    col2.metric("Rotasjon per tank", f"{_rot[0]} uker" if len(_rot) == 1 else f"{_rot[0]}-{_rot[-1]} uker")
else:
    col1.metric("Oppskrifter i rotasjon", f"{meta['n_batches_in_rotation']}")
    col2.metric("Full rotasjon", f"{meta['full_rotasjon_uker']} uker")
col3.metric("Kohorter generert", f"{len(complete_gens)}")

if n_tanker_aktiv > 1:
    # Lagre topp-tetthet per tank (første kohort-runde) for kalibreringsknappen
    _peak_liste = []
    for _t in range(1, n_tanker_aktiv + 1):
        _gens_t = [i for i in complete_gens.values() if i["tank"] == _t]
        _peak_liste.append((_gens_t[0]["stocked"] if _gens_t else 0,
                            max((i["max_density_kg_m3"] for i in _gens_t), default=0.0)))
    st.session_state["_peak_density_per_tank"] = _peak_liste
    # m³-behov per kohort per uke (biomasse / tetthetstak) - brukes i begge moduser
    _n_uker_tot = len(weekly_df.columns)
    _m3_per_kohort = {}
    for _c in cohorts:
        _arr = [0.0] * _n_uker_tot
        for _i in range(_c.n_weeks):
            _wk = _c.start_week + _i
            if _wk < _n_uker_tot:
                _arr[_wk] = _c.weekly_standing_biomass_kg[_i] / cfg.MAX_DENSITY_KG_M3
        _m3_per_kohort[_c.id] = _arr
    m3_behov_uke = pd.DataFrame(_m3_per_kohort, index=list(weekly_df.columns))
    m3_behov_total = m3_behov_uke.sum(axis=1)
    _stabil_tot = m3_behov_total.iloc[52:int(cfg.N_YEARS_TO_RUN) * 52]
    _maks_m3, _maks_uke = float(_stabil_tot.max()), _stabil_tot.idxmax()
    _uker_over = int((m3_behov_total.iloc[:int(cfg.N_YEARS_TO_RUN) * 52] > meta["m3_pool"]).sum())
if n_tanker_aktiv > 1 and cfg.SKOTT_FASTE:
    # ---- FASTE SKOTT: tetthetstaket gjelder PER TANK (fast volum) ----
    n_over_tak = sum(1 for info in complete_gens.values() if info.get("tetthet_over_tak"))
    _maks_tett = max(info["max_density_kg_m3"] for info in complete_gens.values())
    if n_over_tak:
        st.warning(f"Faste skott à {fmt_int(cfg.TANK_VOLUME_M3)} m³: {n_over_tak} av {len(complete_gens)} kohorter "
                   f"overstiger tetthetstaket ({fmt_int(max_density)} kg/m³) i sitt eget skott - høyeste "
                   f"{_maks_tett:.1f} kg/m³. Bruk 'Kalibrer smoltantall'-knappen i sidepanelet, eller juster per tank.")
    else:
        st.caption(f"Faste skott à {fmt_int(cfg.TANK_VOLUME_M3)} m³: alle kohorter holder seg under tetthetstaket "
                   f"({fmt_int(max_density)} kg/m³) i sitt eget skott - høyeste {_maks_tett:.1f} kg/m³.")
    m3_pool = meta["m3_pool"]
elif n_tanker_aktiv > 1:
    # ---- FLEKSIBLE KAKESTYKKER: anleggsnivå-sjekk av m³-behov ----
    # Hver kohort krever biomasse / tetthetstak m³ hver uke; veggene flyttes
    # så alle ligger på (maks) taket. Kravet er at SUM m³-behov over alle
    # kohorter i anlegget holder seg under det samlede volumet (m3_pool).
    m3_pool = meta["m3_pool"]
    _sim_slutt = int(cfg.N_YEARS_TO_RUN) * 52
    if _uker_over:
        st.warning(
            f"Anleggets samlede m³-behov overstiger {fmt_int(m3_pool)} m³ i {_uker_over} uker. "
            f"Topp {fmt_int(_maks_m3)} m³ ({_maks_m3 / m3_pool * 100:.0f} %) i uke {_maks_uke} - "
            f"se 'Kubikkbruk i anlegget' under."
        )
    else:
        st.caption(
            f"Alle kohorter får plass: samlet m³-behov topper på {fmt_int(_maks_m3)} m³ "
            f"({_maks_m3 / m3_pool * 100:.0f} % av {fmt_int(m3_pool)} m³) i uke {_maks_uke}, "
            f"med alle kohorter på maks {fmt_int(max_density)} kg/m³ (fleksible kakestykker)."
        )
else:
    n_over_tak = sum(1 for info in complete_gens.values() if info.get("tetthet_over_tak"))
    if n_over_tak:
        st.warning(f"{n_over_tak} av {len(complete_gens)} kohorter overstiger tetthetstaket "
                   f"({fmt_int(max_density)} kg/m³) - se 'Maks tetthet'-kolonnen under.")
    else:
        st.caption(f"Alle kohorter holder seg under tetthetstaket ({fmt_int(max_density)} kg/m³).")

# ----------------------------------------------------------------------
# MASSEBALANSE - de tre første årene
# Tonn inn (smoltbiomasse kjøpt det året) + netto tilvekst (samme år) =
# tonn levert. NB: for kohorter som starter i ett år og leveres i det
# neste, faller "tonn inn" og "netto tilvekst" på ulike kalenderår hver
# for seg (jf. G4 i eksempeldataene) - så "tonn levert" her er per
# definisjon 1) + 2), IKKE nødvendigvis identisk med faktisk levert
# biomasse akkurat det kalenderåret. Samme prinsipp som massebalanse-
# formlene ellers i Hexacage-prosjektet (jf. formatting.build_mass_balance_equations).
# ----------------------------------------------------------------------
st.subheader("Massebalanse - de tre første årene")
solgt_enhet = "HOG" if cfg.PRODUKTTYPE == "Slaktefisk" else "WFE"
forste_tre_ar = sorted(by_year["ar"].unique())[:3]
for yr in forste_tre_ar:
    row = by_year[by_year["ar"] == yr].iloc[0]
    tonn_inn = row["mengde_smolt"] * cfg.START_WEIGHT_KG / 1000
    netto_tilvekst = row["kg_wfe_netto"] / 1000
    tonn_levert = tonn_inn + netto_tilvekst

    st.markdown(f"**{int(yr)}**")

    if cfg.PRODUKTTYPE == "Slaktefisk":
        # UENDRET fra originalen - IKKE splittet per oppskrift (Slaktefisk
        # har uansett kun én oppskrift, så en splitt ville bare duplisert
        # totalen med en annen beregningsmetode - som i en tidligere runde
        # viste seg å gi et tall som ikke stemte overens med "3) Tonn
        # levert" over, siden de to metodene (ukentlig WFE-akkumulering vs.
        # faktisk leveringsdato) kan telle litt ulikt på tvers av et
        # årsskifte midt i et 8-ukers salgsvindu).
        levert_kg_ar, antall_ar = 0.0, 0.0
        for gid, info in generations.items():
            for batch in info["batches"]:
                _, d_lev = week_label(batch["delivery_week"], cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
                if d_lev.isocalendar()[0] == yr:
                    levert_kg_ar += batch["delivered_biomass_kg"]
                    antall_ar += batch["delivered_count"]
        snittvekt_wfe_g = (levert_kg_ar / antall_ar * 1000) if antall_ar else 0.0
        snittvekt_g = snittvekt_wfe_g * cfg.HOG_FAKTOR
        tonn_levert_hog = tonn_levert * cfg.HOG_FAKTOR
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("1) Tonn inn", f"{fmt_float(tonn_inn, 1)} t")
        c2.metric("2) Netto tilvekst", f"{fmt_float(netto_tilvekst, 1)} t")
        c3.metric("3) Tonn levert (WFE)", f"{fmt_float(tonn_levert, 1)} t")
        c4.metric("Tonn levert (HOG)", f"{fmt_float(tonn_levert_hog, 1)} t")
        c5.metric("Snittvekt levert (WFE)", f"{fmt_float(snittvekt_wfe_g, 0)} g")
        c6.metric("Snittvekt levert (HOG)", f"{fmt_float(snittvekt_g, 0)} g")
        continue

    # Postsmolt (2x/3x/4x): splittet PER OPPSKRIFT - IKKE ett blandet snitt
    # over hele årets leveranser (som tidligere kunne blande f.eks. 629g
    # og 1033g til et meningsløst 781g-"snitt" midt mellom de to, uten at
    # NOEN kohort faktisk veier det).
    alle_oppskrift_numre = sorted(set(info["batch"] for info in generations.values()))
    levert_kg_per_oppskrift, antall_per_oppskrift = {}, {}
    for gid, info in generations.items():
        for batch in info["batches"]:
            _, d_lev = week_label(batch["delivery_week"], cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
            if d_lev.isocalendar()[0] == yr:
                opp = info["batch"]
                levert_kg_per_oppskrift[opp] = levert_kg_per_oppskrift.get(opp, 0.0) + batch["delivered_biomass_kg"]
                antall_per_oppskrift[opp] = antall_per_oppskrift.get(opp, 0.0) + batch["delivered_count"]

    _metric_bokser = [
        ("1) Tonn inn", f"{fmt_int(tonn_inn)} t"),
        ("2) Netto tilvekst", f"{fmt_int(netto_tilvekst)} t"),
        ("3) Tonn levert (totalt)", f"{fmt_int(tonn_levert)} t"),
    ]
    for opp in alle_oppskrift_numre:
        # ALLE oppskriftene vises alltid, selv de årene en gitt oppskrift
        # IKKE hadde noen faktisk levering (rotasjonen treffer ikke alltid
        # nøyaktig likt i hvert kalenderår) - viser "0 t / ingen levering"
        # i stedet for å bare utelate boksen, slik at antall bokser er
        # forutsigbart likt (N oppskrifter) hvert år.
        if opp in levert_kg_per_oppskrift:
            levert_kg = levert_kg_per_oppskrift[opp]
            antall = antall_per_oppskrift[opp]
            snittvekt_wfe_g = (levert_kg / antall * 1000) if antall else 0.0
            snittvekt_g = snittvekt_wfe_g * cfg.HOG_FAKTOR
            tonn_opp_wfe = levert_kg / 1000
            _metric_bokser.append((
                f"Oppskrift {opp}: {fmt_int(tonn_opp_wfe)} t (WFE)",
                f"{fmt_int(snittvekt_g)} g snitt ({solgt_enhet})",
            ))
        else:
            _metric_bokser.append((f"Oppskrift {opp}: 0 t (WFE)", "Ingen levering dette året"))
    # Kompakt, mindre skrift, ALLTID på samme rad uansett hvor mange bokser
    # (3 + N oppskrifter) - st.metric() sin faste, store skriftstørrelse
    # ble for bred til å holde seg på én rad ved 3-4 oppskrifter.
    _bokser_html = "".join(
        f'<div style="flex:1; min-width:0;">'
        f'<div style="font-size:12px; color:var(--text-secondary,#5f5e5a); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{navn}</div>'
        f'<div style="font-size:18px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{verdi}</div>'
        f'</div>'
        for navn, verdi in _metric_bokser
    )
    st.markdown(f'<div style="display:flex; gap:16px; margin-bottom:1rem;">{_bokser_html}</div>', unsafe_allow_html=True)

# ----------------------------------------------------------------------
# SAMLET OVERSIKTSGRAF - stående biomasse, MAB, akkumulert levert biomasse,
# smolt inn, solgt biomasse, forbruk (fôr/oksygen/strøm) - månedlig.
# Rullegardin: én av de fire første oppskriftene, eller alle samlet.
# ----------------------------------------------------------------------
st.subheader("Samlet oversikt")

alle_maneder_full = sorted(pd.to_datetime(ledger["dato"]).dt.strftime("%Y-%m").unique())
antall_ar_i_grafen = st.number_input(
    "Antall år å vise i grafen", min_value=1, max_value=len(alle_maneder_full) // 12 + 1,
    value=3, key="graf_antall_ar",
    help="Modellen kjører hele perioden (se sidepanelet), men grafen begrenses til de "
         "første årene her for å holde den lesbar.",
)
alle_maneder = alle_maneder_full[: antall_ar_i_grafen * 12]

oppskrift_numre = sorted(set(info["batch"] for info in generations.values()))[: max(4, n_tanker_aktiv)]
_alle_label = "Hele anlegget (alle tanker)" if n_tanker_aktiv > 1 else "Alle oppskrifter samlet"
oversikt_options = [f"{gruppe_navn} {n}" for n in oppskrift_numre] + [_alle_label]
oversikt_valg = st.selectbox(
    "Vis oversikt for:",
    options=oversikt_options,
    index=len(oversikt_options) - 1,  # default "Alle oppskrifter samlet" - "Samlet oversikt" bør
                                        # jo faktisk vise ALT som default, ikke bare første oppskrift.
                                        # Velg en enkelt oppskrift her for å isolere KUN dens kohorter.
    key="oversikt_valg",
)

if oversikt_valg == _alle_label:
    gens_vis, ledger_vis = generations, ledger
    _mab_tanker = n_tanker_aktiv   # MAB for hele anlegget = tak x volum x antall tanker
else:
    _mab_tanker = 1
    valgt_n = int(oversikt_valg.split()[-1])
    gens_vis = {gid: info for gid, info in generations.items() if info["batch"] == valgt_n}
    kohort_ider = set(gens_vis.keys())
    vask_ider = {f"({gid})" for gid in kohort_ider}
    ledger_vis = ledger[ledger["kohort_id"].isin(kohort_ider | vask_ider)]

mo = build_monthly_overview(cfg, ledger_vis, gens_vis, all_months=alle_maneder)
mab_t = cfg.MAX_DENSITY_KG_M3 * cfg.TANK_VOLUME_M3 * _mab_tanker / 1000
x = list(range(len(mo)))
width = 0.4

# Bred figur som fyller hele innholdsbredden (layout="wide"). Legenden legges
# under plottet med fast plass (subplots_adjust) i stedet for bbox_inches=
# "tight" - "tight" tar med annotasjoner som stikker utenfor aksene og gjorde
# at selve plottet ble presset sammen i et lite område av bildet.
fig, ax1 = plt.subplots(figsize=(20, 8))
ax1.fill_between(x, mo["standing_biomass_kg"] / 1000, color="lightgrey", label="Stående biomasse (t) - venstre akse")
ax1.plot(x, mo["delivered_biomass_kg_akkumulert"] / 1000, color="purple", linewidth=2,
         label="Akkumulert levert biomasse i året (t) - venstre akse")
ax1.axhline(mab_t, color="black", linestyle="--", linewidth=1.5,
            label=f"Maks tetthet - MAB ({fmt_int(mab_t)} t) - venstre akse")
ax1.plot(x, mo["vekt_g"], color="darkorange", linewidth=2, linestyle=":",
         label="Snittvekt fisk (g, tallverdi på venstre akse) - venstre akse")
ax1.set_ylabel("Tonn (stående/akkumulert biomasse, MAB) / gram (snittvekt)")
ax1.set_ylim(bottom=0)
ax1.set_xticks(x)
ax1.set_xticklabels([month_label(m) for m in mo["maned"]], rotation=45, ha="right", fontsize=8)

# Sparsomme tallmerknader - KUN ved leveringspunktene (der noe faktisk
# skjer), ikke overalt langs linjene - for å gi umiddelbar størrelses-
# forståelse uten å overlesse grafen med tall. Levert størrelse (vekt) og
# pris per smolt vises nå som TO ATSKILTE merknader (i stedet for kun
# vekt før) - prisen beregnes fra samme formel som "Rotasjon"-seksjonen i
# sidepanelet (levert vekt x HOG-faktor x salgspris).
_solgt_enhet_graf = "HOG" if cfg.PRODUKTTYPE == "Slaktefisk" else "WFE"
for i in x:
    if mo["delivered_biomass_kg"].iloc[i] > 0:
        vekt = mo["vekt_g"].iloc[i]
        akkumulert_t = mo["delivered_biomass_kg_akkumulert"].iloc[i] / 1000
        pris_stk = (vekt / 1000.0) * cfg.HOG_FAKTOR * cfg.SALES_PRICE_KR_PER_KG if cfg.SALES_PRICE_KR_PER_KG else None
        ax1.annotate(f"{fmt_int(vekt)} g", xy=(i, vekt), xytext=(i, vekt + mab_t * 0.04),
                     fontsize=8, color="darkorange", ha="center", fontweight="bold")
        ax1.annotate(f"{fmt_int(akkumulert_t)} t", xy=(i, akkumulert_t), xytext=(i + 0.4, akkumulert_t + mab_t * 0.04),
                     fontsize=8, color="purple", ha="left", fontweight="bold")
        if pris_stk is not None:
            ax1.annotate(f"{fmt_float(pris_stk, 2)} kr/stk", xy=(i, vekt), xytext=(i, vekt - mab_t * 0.08),
                         fontsize=8, color="teal", ha="center", fontweight="bold")

ax2 = ax1.twinx()
ax2.bar([i - width / 2 for i in x], mo["smolt_stocked_stk"] / 1000, width=width,
        color="steelblue", alpha=0.7, label="Smolt satt inn (1000 stk) - høyre akse")
ax2.bar([i + width / 2 for i in x], mo["delivered_biomass_kg"] / 1000, width=width,
        color="indianred", alpha=0.7, label="Solgt biomasse (t/mnd) - høyre akse")
ax2.plot(x, mo["feed_kg"] / 1000, color="green", label="Fôrforbruk (t/mnd) - høyre akse")
ax2.plot(x, mo["oksygen_kg"] / 1000, color="goldenrod", label="Oksygenforbruk (t/mnd) - høyre akse")
ax2.plot(x, mo["energi_kwh"] / 1000, color="salmon", label="MGO (1 000 liter/mnd) - høyre akse")
ax2.set_ylabel("1000 stk smolt / tonn / MWh per måned")
ax2.set_ylim(bottom=0)

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper center",
           bbox_to_anchor=(0.5, -0.16), ncol=4, fontsize=9, frameon=False)
ax1.set_xlim(-0.7, len(x) - 0.3)
ax1.margins(x=0)
fig.subplots_adjust(left=0.05, right=0.95, top=0.97, bottom=0.27)
st.pyplot(fig, use_container_width=True, bbox_inches=None)
plt.close(fig)

# ----------------------------------------------------------------------
# KOHORT-SAMMENDRAG
# ----------------------------------------------------------------------
cashflow = build_cashflow_ledger(
    cfg, ledger, generations, cfg.SALES_PRICE_KR_PER_KG, hog_faktor=cfg.HOG_FAKTOR,
    seasonal_index_by_week=seasonal_index_runtime, sales_price_table=_sales_price_table_runtime,
)

st.subheader("Kohort-sammendrag")
rows = []
for gid, info in complete_gens.items():
    lbl_start, _ = week_label(info["start_week"], cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
    lbl_delivery, d_delivery = week_label(info["delivery_week"], cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
    # Salgspris VED LEVERING - hentet direkte fra cashflow (samme tall som
    # faktisk brukes i Kontantstrøm/Resultatregnskap, ikke en duplisert
    # beregning) - summert over ALLE batchene i denne kohorten (relevant
    # for Slaktefisk sine 8 batcher per kohort; for postsmolt med
    # salgsvindu=1 uke er det uansett bare én batch).
    _kohort_inntekt = cashflow.loc[cashflow["kohort_id"] == gid, "inntekt_kr"].sum()
    _kohort_kg_solgt = cashflow.loc[cashflow["kohort_id"] == gid, "kg_solgt"].sum()
    # Antall/tonn levert og snittvekt = SUM OVER BATCHENE (det som faktisk
    # selges uke for uke i salgsvinduet) - IKKE "hele bestanden ved siste
    # vekstuke" (info["delivered_biomass_kg"]), som overvurderer både tonn
    # og vekt fordi 7 av 8 batcher selges FØR siste uke. Samme tall som
    # kontantstrøm, månedstabellen under og grafene bruker.
    _antall_levert = round(sum(b["delivered_count"] for b in info["batches"]))
    _levert_kg = sum(b["delivered_biomass_kg"] for b in info["batches"])
    _snittvekt_levert_g = _levert_kg / _antall_levert * 1000 if _antall_levert else 0.0
    _salgspris_kr_kg = _kohort_inntekt / _kohort_kg_solgt if _kohort_kg_solgt else 0.0
    _salgspris_kr_stk = _kohort_inntekt / _antall_levert if _antall_levert else 0.0
    rows.append({
        "Kohort": gid, gruppe_navn: info["batch"], "År (levering)": d_delivery.isocalendar()[0],
        "Innsett (uke)": lbl_start, "Levering (uke)": lbl_delivery,
        "Vekst-uker": info["growth_weeks"], "Vask-uker": info["cleaning_weeks"],
        "Smoltvekt inn (g)": round(info.get("start_weight_kg", cfg.START_WEIGHT_KG) * 1000, 1),
        "Smoltantall": info["stocked"],
        "Antall levert (stk)": _antall_levert,
        "Snittvekt levert (g WFE)": round(_snittvekt_levert_g),
        "Snittvekt levert (g HOG)": round(_snittvekt_levert_g * cfg.HOG_FAKTOR),
        "Leveringsvekt (g)": round(info["delivery_weight_kg"] * 1000),
        "Salgspris ved levering (kr/kg)": round(_salgspris_kr_kg, 2),
        "Salgspris ved levering (kr/stk)": round(_salgspris_kr_stk, 2),
        "Bruttovekst (kg WFE)": round(info["total_gross_growth_kg"], 1),
        "Levert (t WFE)": round(_levert_kg / 1000, 1),
        "Levert (t HOG)": round(_levert_kg * cfg.HOG_FAKTOR / 1000, 1),
        # FCR = fôr / tilvekst. WFE-versjonen er den biologiske (fôr per kg
        # levende tilvekst). HOG-versjonen måler samme fôr mot SELLBAR
        # tilvekst (x HOG-faktor) - alltid høyere, siden hode/innvoller
        # ikke selges. Samme prinsipp som i konsolidert kontantstrøm.
        "FCR (WFE)": round(info["overall_fcr"], 2),
        "FCR (HOG)": round(info["overall_fcr"] / cfg.HOG_FAKTOR, 2) if cfg.HOG_FAKTOR else None,
        **({"Maks m³-behov (ved tak)": round(info["max_m3_behov"])} if (n_tanker_aktiv > 1 and not cfg.SKOTT_FASTE) else {
            "Maks tetthet (kg/m³)": round(info["max_density_kg_m3"], 1),
            "Over tak?": "Ja" if info.get("tetthet_over_tak") else "",
        }),
    })
cohort_df = pd.DataFrame(rows)
cohort_df_display = cohort_df.copy()
cohort_df_display["Smoltantall"] = cohort_df_display["Smoltantall"].apply(fmt_int)
cohort_df_display["Antall levert (stk)"] = cohort_df_display["Antall levert (stk)"].apply(fmt_int)
cohort_df_display["Leveringsvekt (g)"] = cohort_df_display["Leveringsvekt (g)"].apply(fmt_int)
cohort_df_display["Snittvekt levert (g WFE)"] = cohort_df_display["Snittvekt levert (g WFE)"].apply(fmt_int)
cohort_df_display["Snittvekt levert (g HOG)"] = cohort_df_display["Snittvekt levert (g HOG)"].apply(fmt_int)
cohort_df_display = cohort_df_display.rename(columns={"Leveringsvekt (g)": "Vekt siste batch (g WFE)"})
cohort_df_display["Salgspris ved levering (kr/kg)"] = cohort_df_display["Salgspris ved levering (kr/kg)"].apply(lambda x: fmt_float(x, 2))
cohort_df_display["Salgspris ved levering (kr/stk)"] = cohort_df_display["Salgspris ved levering (kr/stk)"].apply(lambda x: fmt_float(x, 2))
cohort_df_display["Bruttovekst (kg WFE)"] = cohort_df_display["Bruttovekst (kg WFE)"].apply(lambda x: fmt_float(x, 1))
cohort_df_display["Smoltvekt inn (g)"] = cohort_df_display["Smoltvekt inn (g)"].apply(lambda x: fmt_float(x, 1))
cohort_df_display["Levert (t WFE)"] = cohort_df_display["Levert (t WFE)"].apply(lambda x: fmt_float(x, 1))
cohort_df_display["Levert (t HOG)"] = cohort_df_display["Levert (t HOG)"].apply(lambda x: fmt_float(x, 1))
cohort_df_display["FCR (WFE)"] = cohort_df_display["FCR (WFE)"].apply(lambda x: fmt_float(x, 2))
cohort_df_display["FCR (HOG)"] = cohort_df_display["FCR (HOG)"].apply(lambda x: fmt_float(x, 2) if x is not None else "")
if "Maks tetthet (kg/m³)" in cohort_df_display.columns:
    cohort_df_display["Maks tetthet (kg/m³)"] = cohort_df_display["Maks tetthet (kg/m³)"].apply(lambda x: fmt_float(x, 1))
if "Maks m³-behov (ved tak)" in cohort_df_display.columns:
    cohort_df_display["Maks m³-behov (ved tak)"] = cohort_df_display["Maks m³-behov (ved tak)"].apply(fmt_int)
cohort_df_wide = cohort_df_display.set_index("Kohort").T
cohort_df_wide.index.name = "Felt"
_render_table(cohort_df_wide)

# ----------------------------------------------------------------------
# KOHORTUTVIKLING MÅNED FOR MÅNED - én kohort om gangen (G1-K1 ... ), 12
# månedskolonner + "Hele året" som 13. kolonne, én blokk per kalenderår
# kohorten står i tanken. Beholdningsrader (biomasse, antall, vekt,
# tetthet, m3 brukt, % av tank) = verdi ved månedens slutt; flow-rader
# (fôr, bruttovekst, levert) = sum i måneden. Årskolonnen: sum for flows,
# MAKS i året for beholdning (høyeste tetthet/m3-behov det året).
# ----------------------------------------------------------------------
st.subheader("Kohortutvikling måned for måned")
_kohort_utv_valg = st.selectbox(
    "Velg kohort:", options=list(complete_gens.keys()), index=0, key="kohort_utvikling_valg",
    help="G = generasjon (runde innsett i anlegget), K = kohort/utsett-nummer i runden (= tank).",
)
_ku_info = complete_gens[_kohort_utv_valg]
_ku = ledger[ledger["kohort_id"] == _kohort_utv_valg].copy()
_ku["_dato"] = pd.to_datetime(_ku["dato"])
# År/måned bestemmes av ukens TORSDAG (ISO-konvensjonen): uke 1 i 2026
# starter mandag 29.12.2025, og skal telle som januar 2026 - ikke gi en
# egen, nesten tom "2025"-blokk.
_ku["_ar"] = (_ku["_dato"] + pd.Timedelta(days=3)).dt.year
_ku["_mnd"] = (_ku["_dato"] + pd.Timedelta(days=3)).dt.month
_ku = _ku.sort_values("_dato")
_ku["m3_brukt"] = _ku["biomasse_kg"] / cfg.MAX_DENSITY_KG_M3       # m3 som trengs ved tetthetstaket
_ku["pct_av_tank"] = _ku["m3_brukt"] / cfg.TANK_VOLUME_M3 * 100
_ku["tetthet"] = _ku["biomasse_kg"] / cfg.TANK_VOLUME_M3
_beholdning = {  # kolonne -> (radnavn, desimaler)
    "biomasse_kg": ("Stående biomasse (t)", 1), "antall_fisk": ("Antall fisk (stk)", 0),
    "vekt_g": ("Snittvekt (g)", 0), "tetthet": ("Tetthet (kg/m³)", 1),
    "m3_brukt": (f"m³-behov (ved {fmt_int(cfg.MAX_DENSITY_KG_M3)} kg/m³)", 0),
    "pct_av_tank": (f"% av anleggets {fmt_int(meta.get('m3_pool', cfg.TANK_VOLUME_M3))} m³", 1),
}
_m3_ref = meta.get("m3_pool", cfg.TANK_VOLUME_M3)
_ku["pct_av_tank"] = _ku["m3_brukt"] / _m3_ref * 100
_flow = {
    "mengde_for": ("Fôr (t)", 1), "kg_wfe_brutto": ("Bruttovekst (t WFE)", 1),
    "kg_wfe_levert": ("Levert (t WFE)", 1),
}
_mnd_navn = ["Jan", "Feb", "Mar", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Des"]
st.caption(
    f"{_kohort_utv_valg}: innsett {week_label(_ku_info['start_week'], cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)[0]}, "
    f"levering {week_label(_ku_info['delivery_week'], cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)[0]}. "
    "Beholdningsrader = verdi ved månedens slutt (årskolonnen viser MAKS i året); fôr/vekst/levert = sum "
    "i måneden (årskolonnen = sum). 'm³-behov' = biomassen delt på tetthetstaket, dvs. det volumet "
    "kakestykket må ha for at kohorten skal ligge på taket - veggene flyttes deretter."
)
for _ar in sorted(_ku["_ar"].unique()):
    _ku_ar = _ku[_ku["_ar"] == _ar]
    _kol = [f"{m} {_ar}" for m in _mnd_navn] + [f"Hele {_ar}"]
    _rader = {}
    for kol, (navn, dec) in _beholdning.items():
        _slutt = _ku_ar.groupby("_mnd")[kol].last()
        _vals = [(_slutt[m] / (1000 if kol == "biomasse_kg" else 1)) if m in _slutt.index else None for m in range(1, 13)]
        _maks = max(v for v in _vals if v is not None) if any(v is not None for v in _vals) else None
        _rader[navn] = [fmt_float(v, dec) if v is not None else "" for v in _vals] + [fmt_float(_maks, dec) if _maks is not None else ""]
    for kol, (navn, dec) in _flow.items():
        _sum = _ku_ar.groupby("_mnd")[kol].sum() / 1000.0
        _vals = [_sum[m] if m in _sum.index else None for m in range(1, 13)]
        _tot = sum(v for v in _vals if v is not None)
        _rader[navn] = [fmt_float(v, dec) if v is not None and v != 0 else "" for v in _vals] + [fmt_float(_tot, dec)]
    _ku_wide = pd.DataFrame(_rader, index=_kol).T
    _ku_wide.index.name = "Felt"
    st.markdown(f"**{_ar}**")
    _render_table(_ku_wide, highlight_groups=[
        {"rows": [_beholdning["m3_brukt"][0], _beholdning["pct_av_tank"][0]], "bg": "#eef1f6", "text": "#5b6b82"},
    ])

# ----------------------------------------------------------------------
# KUBIKKBRUK I ANLEGGET - fleksible kakestykker: m³-behov per kohort
# (biomasse / tetthetstak) stablet, mot anleggets samlede volum. Ukentlig
# graf + månedstabell (verdi ved månedens slutt, 13. kolonne = maks i året).
# ----------------------------------------------------------------------
if n_tanker_aktiv > 1:
    if cfg.SKOTT_FASTE:
        st.subheader("Kubikkbruk i anlegget (faste skott)")
        st.caption(
            f"Faste skott à {fmt_int(cfg.TANK_VOLUME_M3)} m³: hver kohort er låst til sitt eget skott, så "
            f"'m³-behov' (biomasse / {fmt_int(max_density)} kg/m³) kan aldri overstige {fmt_int(cfg.TANK_VOLUME_M3)} m³ "
            f"per kohort. Grafen/kakene viser hvor mye av anleggets {fmt_int(m3_pool)} m³ som faktisk er utnyttet "
            "- resten er ubrukt volum i skottene, fordi veggene ikke kan flyttes."
        )
    else:
        st.subheader("Kubikkbruk i anlegget (fleksible kakestykker)")
        st.caption(
            f"Hver kohort tildeles det volumet biomassen krever ved {fmt_int(max_density)} kg/m³ - veggene "
            f"flyttes fortløpende (også nedover etter hvert som batcher slaktes). Summen over alle kohorter "
            f"må holde seg under anleggets {fmt_int(m3_pool)} m³."
        )
    _m3_alle_ar = sorted({(pd.to_datetime(d) + pd.Timedelta(days=3)).year for d in weekly_df.loc["Dato"]})[: int(cfg.N_YEARS_TO_RUN)]
    _c_a, _c_b = st.columns(2)
    _m3_startar = int(_c_a.selectbox("Kubikkbruk måned for måned - startår:", options=_m3_alle_ar, index=0, key="m3_ar_valg"))
    _m3_n_ar = int(_c_b.number_input("Antall år å vise", min_value=1, max_value=len(_m3_alle_ar), value=min(3, len(_m3_alle_ar)), key="m3_n_ar"))
    _m3_ar_liste = [a for a in _m3_alle_ar if _m3_startar <= a < _m3_startar + _m3_n_ar]
    _m3_uker = [lbl for lbl in weekly_df.columns][: antall_ar_i_grafen * 52]
    _m3_vis = m3_behov_uke.loc[_m3_uker]
    _m3_vis = _m3_vis.loc[:, (_m3_vis != 0).any(axis=0)]
    fig_m3, ax_m3 = plt.subplots(figsize=(20, 7))
    # Farge = TANK (K-nummeret): G1-K1, G2-K1, G3-K1 ... får samme farge, så
    # hver tank kan følges fra generasjon til generasjon. Gjelder både her
    # og i kakediagrammene under.
    def _tankfarge(kohort_id):
        try:
            k = int(str(kohort_id).split("-K")[1]) - 1
        except (IndexError, ValueError):
            k = 0
        return plt.cm.tab10(k % 10)
    ax_m3.stackplot(range(len(_m3_uker)), *[_m3_vis[c].values / 1000 for c in _m3_vis.columns],
                    labels=list(_m3_vis.columns), colors=[_tankfarge(c) for c in _m3_vis.columns], alpha=0.85)
    ax_m3.axhline(m3_pool / 1000, color="black", linestyle="--", linewidth=1.5, label=f"Anleggets volum ({fmt_int(m3_pool)} m³)")
    ax_m3.set_ylabel("m³-behov (1 000 m³)")
    _ticks = list(range(0, len(_m3_uker), 13))
    ax_m3.set_xticks(_ticks)
    ax_m3.set_xticklabels([_m3_uker[i] for i in _ticks], rotation=45, ha="right", fontsize=8)
    ax_m3.set_ylim(bottom=0)
    ax_m3.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=8, fontsize=8, frameon=False)
    ax_m3.set_xlim(0, len(_m3_uker) - 1)
    fig_m3.subplots_adjust(left=0.05, right=0.98, top=0.97, bottom=0.28)
    st.pyplot(fig_m3, use_container_width=True, bbox_inches=None)
    plt.close(fig_m3)

    # ---- KAKEDIAGRAM PER MÅNED: hvordan de fleksible kakestykkene må
    # fordeles (m³-behov per kohort ved månedens slutt) + hvit "Ledig"-del.
    # 12 per år, for startåret og året etter (24 totalt), samme farger som
    # i stablet-grafen over. ----
    _pie_ar = [a for a in _m3_alle_ar if _m3_startar <= a < _m3_startar + 2]
    _alle_kohorter_farge = {c: _tankfarge(c) for c in m3_behov_uke.columns}
    _pie_df = m3_behov_uke.copy()
    _pie_df["_dato"] = pd.to_datetime([weekly_df.loc["Dato", lbl] for lbl in _pie_df.index]) + pd.Timedelta(days=3)
    _pie_df = _pie_df[_pie_df["_dato"].dt.year.isin(_pie_ar)]
    _pie_df["_ar"], _pie_df["_mnd"] = _pie_df["_dato"].dt.year, _pie_df["_dato"].dt.month
    _pie_slutt = _pie_df.groupby(["_ar", "_mnd"]).last().drop(columns=["_dato"])
    st.caption(
        f"Kakestykkene måned for måned ({_pie_ar[0]}-{_pie_ar[-1]}): hver kohorts andel av anleggets "
        f"{fmt_int(m3_pool)} m³ ved månedens slutt, hvit del = ledig kapasitet. Viser hvor mye veggene må "
        "flyttes fra måned til måned."
    )
    fig_pie, axes = plt.subplots(len(_pie_ar) * 2, 6, figsize=(20, 7.0 * len(_pie_ar)))
    axes = axes.flatten()
    _i = 0
    for _ar in _pie_ar:
        for m in range(1, 13):
            ax = axes[_i]; _i += 1
            if (_ar, m) not in _pie_slutt.index:
                ax.axis("off"); continue
            rad = _pie_slutt.loc[(_ar, m)]
            rad = rad[rad > 0]
            # FAST plass i kaken: alltid K1 først (kl. 12, med klokka), så K2,
            # K3 ... K6, deretter "Ledig" - uansett hvilken generasjon som
            # står i tanken. Da ligger hver tank på samme sted hver måned.
            rad = rad[sorted(rad.index, key=lambda c: (int(str(c).split("-K")[1]), str(c)))]
            verdier = list(rad.values) + [max(m3_pool - rad.sum(), 0.0)]
            etiketter = [f"{c}\n{v / 1000:.0f}k" for c, v in rad.items()] + [f"Ledig\n{max(m3_pool - rad.sum(), 0) / 1000:.0f}k"]
            farger = [_alle_kohorter_farge[c] for c in rad.index] + ["white"]
            ax.pie(verdier, labels=etiketter, colors=farger, startangle=90, counterclock=False,
                   wedgeprops={"edgecolor": "#444", "linewidth": 0.6}, textprops={"fontsize": 6})
            _brukt_pct = rad.sum() / m3_pool * 100
            ax.set_title(f"{_mnd_navn[m - 1]} {_ar} - {_brukt_pct:.0f} % brukt", fontsize=8)
    fig_pie.tight_layout()
    st.pyplot(fig_pie, use_container_width=True)
    plt.close(fig_pie)

    # Sammenhengende månedskolonner over valgte år, med "Maks <år>" som 13.
    # kolonne etter desember hvert år. Fryst radnavn-kolonne (se _render_table).
    _m3_df = m3_behov_uke.copy()
    _m3_df["_dato"] = pd.to_datetime([weekly_df.loc["Dato", lbl] for lbl in _m3_df.index]) + pd.Timedelta(days=3)
    _m3_df = _m3_df[_m3_df["_dato"].dt.year.isin(_m3_ar_liste)]
    _m3_df["_ar"], _m3_df["_mnd"] = _m3_df["_dato"].dt.year, _m3_df["_dato"].dt.month
    _m3_slutt = _m3_df.groupby(["_ar", "_mnd"]).last().drop(columns=["_dato"])
    _m3_slutt = _m3_slutt.loc[:, (_m3_slutt != 0).any(axis=0)]
    _m3_kol, _m3_rader = [], {cid: [] for cid in _m3_slutt.columns}
    _m3_rader.update({"Sum m³-behov": [], f"% av {fmt_int(m3_pool)} m³": [], "Ledig m³": []})
    for _ar in _m3_ar_liste:
        _tot_ar, _maks_per_cid = [], {cid: 0.0 for cid in _m3_slutt.columns}
        for m in range(1, 13):
            _m3_kol.append(f"{_mnd_navn[m - 1]} {_ar}")
            _tot = 0.0
            for cid in _m3_slutt.columns:
                v = _m3_slutt.loc[(_ar, m), cid] if (_ar, m) in _m3_slutt.index else 0.0
                _m3_rader[cid].append(fmt_int(v) if v > 0 else "")
                _maks_per_cid[cid] = max(_maks_per_cid[cid], v)
                _tot += v
            _tot_ar.append(_tot)
            _m3_rader["Sum m³-behov"].append(fmt_int(_tot))
            _m3_rader[f"% av {fmt_int(m3_pool)} m³"].append(fmt_float(_tot / m3_pool * 100, 1))
            _m3_rader["Ledig m³"].append(fmt_int(m3_pool - _tot))
        _m3_kol.append(f"Maks {_ar}")
        for cid in _m3_slutt.columns:
            _m3_rader[cid].append(fmt_int(_maks_per_cid[cid]) if _maks_per_cid[cid] > 0 else "")
        _m3_rader["Sum m³-behov"].append(fmt_int(max(_tot_ar)))
        _m3_rader[f"% av {fmt_int(m3_pool)} m³"].append(fmt_float(max(_tot_ar) / m3_pool * 100, 1))
        _m3_rader["Ledig m³"].append(fmt_int(m3_pool - max(_tot_ar)))
    _m3_wide = pd.DataFrame(_m3_rader, index=_m3_kol).T
    _m3_wide.index.name = "Felt"
    _render_table(_m3_wide, highlight_groups=[
        {"rows": ["Sum m³-behov", f"% av {fmt_int(m3_pool)} m³", "Ledig m³"], "bg": "#eef1f6", "text": "#5b6b82"},
    ])

# ----------------------------------------------------------------------
# ÅRLIG INDEKSERINGSTABELL - viser de faktiske eskalerte prisene per år.
# Selve beregningen (i build_resource_ledger/build_cashflow_ledger) bruker
# ALLEREDE disse satsene internt - denne tabellen er kun til innsyn/kontroll.
# ----------------------------------------------------------------------
with st.expander("📈 Årlig indeksering (klikk for å vise/skjule)", expanded=False):
    st.caption(
        "Inntekt og kostnadskomponentene (0-12) eskaleres årlig, fast fra og med 1. januar - "
        "satsene justeres i sidepanelet under 'Årlig indeksering'. Tabellen viser den FAKTISKE "
        "prisen som brukes hvert år (år 1 = basisår, ingen eskalering ennå)."
    )
    eskalering_ar = sorted(pd.to_datetime(ledger["dato"]).dt.year.unique())
    eskalering_tabell = build_escalation_table(cfg, eskalering_ar, extra_lines=eskalering_ekstra_linjer)
    eskalering_display = eskalering_tabell.rename(columns={"linje": "Felt"})
    eskalering_kolonner = [str(y) for y in eskalering_ar]

    # To grupper, to ulike presisjoner: inntekt/0-12 er SMÅ kr/kg-priser der
    # desimaler er meningsfulle (0,50 kr/kg er ikke det samme som 1 kr/kg),
    # mens 13.1-13.9/14/15 er STORE kronebeløp der desimaler bare er støy.
    ekstra_navn = {el["navn"] for el in eskalering_ekstra_linjer}
    er_ekstra = eskalering_display["Felt"].isin(ekstra_navn)
    del_pris = with_thousands(eskalering_display[~er_ekstra], int_cols=[], float_cols=eskalering_kolonner, float_decimals=2)
    del_kr = with_thousands(eskalering_display[er_ekstra], int_cols=[], float_cols=eskalering_kolonner, float_decimals=0)
    eskalering_display = pd.concat([del_pris, del_kr])

    for kol in eskalering_display.columns:
        eskalering_display[kol] = eskalering_display[kol].apply(
            lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else v
        )
    eskalering_wide = eskalering_display.set_index("Felt")
    eskalering_wide.index.name = "Felt"
    _render_table(eskalering_wide)

# ----------------------------------------------------------------------
# RESSURSREGNSKAP
# ----------------------------------------------------------------------
with st.expander("📊 Ressursregnskap (klikk for å vise/skjule)", expanded=False):
    st.caption(
        "Smolt, fôr (Skretting-modellen), MGO og oksygen (kg WFE-baserte "
        "faktorer), samt de resterende COGS-linjene (annet direkte materiell/lønn, "
        "slakt, distribusjon, indirekte materiell/lønn, andre produksjonskostnader) "
        "- foreløpig én flat enhet = 1 x kg WFE produsert for de siste. "
        "kr-kolonner vises kun når en pris er satt i sidepanelet."
    )
    tab1, tab2, tab3, tab4 = st.tabs(["Ukentlig", "Måned", "Per år", "Per kohort"])

    with tab1:
        year_options = sorted(pd.to_datetime(ledger["dato"]).dt.isocalendar().year.unique())
        yr_pick = st.selectbox("Vis uker i år:", options=["Alle år"] + list(year_options), key="ledger_year")
        show = ledger if yr_pick == "Alle år" else ledger[pd.to_datetime(ledger["dato"]).dt.isocalendar().year == yr_pick]
        if n_tanker_aktiv > 1:
            # Flere tanker deler samme uke -> én rad per (tank, uke). Velg
            # én tank (radene vises som før), eller hele anlegget summert
            # per uke (mengder/kr/biomasse/antall summeres; vekt = vektet
            # snitt over tankene; ID-felt viser hvilke kohorter som var inne).
            _tank_valg = st.selectbox(
                "Vis ukentlig for:", options=["Hele anlegget (sum per uke)"] + [f"Tank {t}" for t in range(1, n_tanker_aktiv + 1)],
                key="ledger_tank",
            )
            if _tank_valg.startswith("Tank"):
                _t = int(_tank_valg.split()[-1])
                show = show[show["kohort_id"].str.replace("(", "", regex=False).str.replace(")", "", regex=False)
                            .str.endswith(f"-K{_t}")]  # G{n}-K{t}: K = tank/utsett-nr
            else:
                _num = [c for c in show.columns if c not in ("kohort_id", "batch_id", "uke", "dato", "fase", "vekt_g")]
                _g = show.groupby("uke", sort=False)
                _sum = _g[_num].sum(min_count=1)
                _bio = _g["biomasse_kg"].sum()
                _vekt = (_g.apply(lambda d: (d["vekt_g"].fillna(0) * d["biomasse_kg"]).sum()) / _bio.replace(0, float("nan")))
                _ids = _g["kohort_id"].apply(lambda x: ", ".join(x))
                show = _sum.copy()
                show["vekt_g"] = _vekt.round(1)
                show.insert(0, "dato", _g["dato"].first())
                show.insert(0, "fase", _g["fase"].apply(lambda x: "Vekst" if (x == "Vekst").any() else "Vask"))
                show.insert(0, "batch_id", "")
                show.insert(0, "kohort_id", _ids)
                show = show.reset_index()[list(ledger.columns)]
        show_wide = _transpose_for_display(show, "uke")
        _render_table(show_wide)

    with tab2:
        month_labels = by_month["maned"].apply(month_label)
        month_wide = _transpose_for_display(by_month, "maned", period_labels=month_labels)
        _render_table(month_wide)

    with tab3:
        year_wide = _transpose_for_display(by_year, "ar")
        _render_table(year_wide)

        fig, ax = plt.subplots(figsize=(7, 3.2))
        ax.bar(by_year["ar"].astype(str), by_year["mengde_for"] / 1000)
        ax.set_ylabel("Fôr (tonn)")
        ax.set_title("Fôrforbruk per år")
        st.pyplot(fig)

    with tab4:
        cohort_wide = _transpose_for_display(by_cohort, "kohort_id")
        _render_table(cohort_wide)

# ----------------------------------------------------------------------
# KONTANTSTRØM - egen seksjon nederst, atskilt fra ressursregnskapets
# faner over. Rullegardin: uke/måned/år/batch. Inntekt (kr) = levert
# biomasse x salgspris (satt i sidepanelet, "Salg"-seksjonen), kun i
# leveringsuken. Akkumulert kontantstrøm nullstilles ved hver kohorts
# første vekstuke - "akkumulert frem til leveranse".
#
# BATCH vs. KOHORT: en kohort er hele utsettingshendelsen; en batch er ett
# enkelt salgsuttak innenfor kohortens slaktevindu (jf. Hexacage_6tank_
# matfisk_notat.md). I DENNE 1-tank-modellen selges hele kohorten i ÉN
# leveranse, så det er nøyaktig én batch per kohort ennå (kohort "K1" =
# batch "K1-B1"). Batchoversikten under er derfor identisk med kohort-
# oversikten i praksis nå, men bygget på batch_id-feltet slik at en
# fremtidig utvidelse til delvis/gradert slakting (flere batcher per
# kohort) bare krever flere batch_id-verdier, ikke en ny tabellstruktur.
# Merk: dette er kontantstrøm og kostnadskomponenter (PnL-linjer) - en
# egen BALANSE (biologisk eiendel, balanseført verdi) er IKKE bygget ennå.
# ----------------------------------------------------------------------
# VIKTIG: bygges fra weekly_df (HELE, sammenhengende kalenderperioden - se
# scheduler_1tank._assemble_wide_table(), som løper uke for uke fra 0 til
# n_weeks_total UANSETT), IKKE fra ledger (som bare har rader for ukene en
# kohort faktisk opptar). Med ekstra venteuker mellom oppskrifter (se
# "Rotasjon" i sidepanelet) kan tanken nå stå LEDIG i perioder - faste
# kostnader (leie, lønn osv.) skal fortsatt løpe da (rent stopper jo ikke
# fordi tanken er tom), så all_weeks_uke_dato MÅ dekke også de ledige
# ukene, ellers ville fixed_costs_weekly (bygget over denne listen)
# stille droppet kostnadene for akkurat de ukene.
all_weeks_uke_dato = [(lbl, pd.to_datetime(weekly_df.loc["Dato", lbl]).date()) for lbl in weekly_df.columns]

# "13.5 Desinfeksjon" er hendelsesbasert (NOK per kohort/generasjon, IKKE en
# jevn ukentlig sats) - bygges her fra de FAKTISKE kohortene som genereres,
# med beløpet lagt i uken hver kohort settes inn (start_week).
desinfeksjon_ukentlig = {}
for gid, info in generations.items():
    lbl, _ = week_label(info["start_week"], cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
    desinfeksjon_ukentlig[lbl] = desinfeksjon_ukentlig.get(lbl, 0.0) + desinfeksjon_per_kohort_kr

def _legg_til_maneder(d: date, maneder: int) -> date:
    manedsindeks = d.month - 1 + maneder
    ar = d.year + manedsindeks // 12
    maned = manedsindeks % 12 + 1
    siste_dag_i_maned = calendar.monthrange(ar, maned)[1]
    return date(ar, maned, min(d.day, siste_dag_i_maned))

# "13.2 Oppankring" er et NEDBETALT lån. Renter/avdrag beregnes UKE FOR UKE
# (ikke en flat 52-ukers-antakelse klippet ved en kalenderdato - det ga
# tidligere et lite, men reelt avvik mot balansen, siden lånesaldoen der
# regnes fra de FAKTISKE kalendermånedenes ukeantall). "kr_leie_132" (den
# faktiske kontantbetalingen) bygges fra NØYAKTIG samme kilde som
# lånesaldoen i balansen - se build_renter_avdrag_per_uke().
prosjekt_start_dato = pd.to_datetime(all_weeks_uke_dato[0][1]).date()
renter_avdrag_per_uke = build_renter_avdrag_per_uke(
    oppankring_inv, oppankring_rente, int(oppankring_mnd), prosjekt_start_dato,
    [u for u, _ in all_weeks_uke_dato], dict(all_weeks_uke_dato),
)
oppankring_ukentlig = dict(zip(
    renter_avdrag_per_uke["uke"], renter_avdrag_per_uke["renter_uke"] + renter_avdrag_per_uke["avdrag_uke"],
))
oppankring_sluttdato = _legg_til_maneder(prosjekt_start_dato, int(oppankring_mnd))

# Faste kostnader (13-16) - beregnet HER (før Kontantstrøm-seksjonen) fordi
# både Batch-/Kohortoversikten OG Konsolidert kontantstrøm lenger ned bruker
# denne - se _show_kontantstrom_kombinert() sitt fixed_costs_weekly-argument.
# 13.1 Kapitalleie med eventuelle NYE SATSER underveis (ny TC): per uke =
# CAPEX x gjeldende sats x eskalering fra det året satsen begynte å gjelde.
# Bygges her (event-basert) slik at Konsolidert kontantstrøm, Resultat,
# Balanse OG Utleier-modellen alle får samme tall.
_esc_131 = cfg.ESCALATION_RATES_BY_YEAR.get("leie_131", {})
# Ny kapitalleie-sats per reforhandling: ny 13.1 = dagens eskalerte 13.1 det
# året + andel x (EBITDA-krav fra nybyggparitet - dagens 13.1). Sekvensielt:
# reforhandling 2 tar utgangspunkt i TC-en ETTER reforhandling 1.
_sats_gj, _basis_gj = kapitalleie_pct, int(cfg.ESCALATION_BASE_YEAR)
for _rf in sorted(reforhandlinger, key=lambda r: r["ar"]):
    _y = _rf["ar"]
    _old_131 = escalate_price_by_year(capex * _sats_gj, _esc_131, _y, _basis_gj) or 0.0
    _krav_131 = capex * (1 + byggeindeks_pct_ar) ** (_y - int(cfg.START_ISO_YEAR)) * ebitda_yield_pct
    _ny_131 = _old_131 + _rf["andel"] * (_krav_131 - _old_131)
    _ny_sats = _ny_131 / capex if capex else 0.0
    kapitalleie_reprising.append({"ar": _y, "sats_pct": _ny_sats, "andel": _rf["andel"],
                                  "gammel_131": _old_131, "krav_131": _krav_131, "ny_131": _ny_131})
    _sats_gj, _basis_gj = _ny_sats, _y
_rp_aktive = sorted([r for r in kapitalleie_reprising if r["ar"] > 0], key=lambda r: r["ar"])
if _rp_aktive:
    kapitalleie_ukentlig = {}
    for _u, _d in all_weeks_uke_dato:
        _y = int(pd.to_datetime(_d).isocalendar()[0])   # ISO-år - samme årsinndeling som aggregeringen
        _sats, _basis = kapitalleie_pct, int(cfg.ESCALATION_BASE_YEAR)
        for _r in _rp_aktive:
            if _y >= _r["ar"]:
                _sats, _basis = _r["sats_pct"], _r["ar"]
        kapitalleie_ukentlig[_u] = escalate_price_by_year(capex * _sats / 52.0, _esc_131, _y, _basis) or 0.0
    _event_kr = {"leie_135": desinfeksjon_ukentlig, "leie_132": oppankring_ukentlig, "leie_131": kapitalleie_ukentlig}
else:
    _event_kr = {"leie_135": desinfeksjon_ukentlig, "leie_132": oppankring_ukentlig}
fixed_costs_weekly = build_fixed_costs_weekly(cfg, all_weeks_uke_dato, event_based_kr=_event_kr)

st.subheader("Kontantstrøm")
if not cfg.SALES_PRICE_KR_PER_KG and not _sales_price_table_runtime:
    st.caption("Ingen salgspris satt i sidepanelet ennå (under 'Salg') - inntektsraden viser 0 inntil den er fylt inn.")
st.caption("Kostnadene er splittet opp i komponentene 0-12, samme linjer som i ressursregnskapet over.")


def _cf_row_labels(cfg):
    solgt_enhet = "HOG" if cfg.PRODUKTTYPE == "Slaktefisk" else "WFE"
    labels = {"kohort_id": "Kohort", "batch_id": "Batch", "dato": "Dato", "fase": "Fase", "inntekt_kr": "Inntekt (kr)"}
    for r in cfg.RESOURCES:
        labels[f"kr_{r['id']}"] = f"{r['navn']} (kr)"
    labels.update({
        "kostnad_totalt_kr": "Kostnad totalt (kr)",
        "netto_kontantstrom_kr": "Netto kontantstrøm (kr)",
        "akkumulert_kontantstrom_kr": "Akkumulert kontantstrøm (kr)",
        "kg_wfe_brutto": "Bruttovekst i perioden (kg WFE) - grunnlag for kostnadslinjene",
        "kg_wfe_levert": "Levert kunde, denne perioden (kg WFE)",
        "kg_wfe_netto_akkumulert": "Netto tilvekst - levert minus innkjøpt (kg WFE)",
        "kg_wfe_levert_akkumulert": "Levert kunde, akkumulert (kg WFE)",
        "kg_solgt": f"Solgt vekt, denne perioden (kg {solgt_enhet})",
        "kg_solgt_akkumulert": f"Solgt vekt, akkumulert (kg {solgt_enhet})",
    })
    return labels


def _show_cashflow_table(df, period_col, period_labels=None):
    wide = _transpose_for_display(df, period_col, period_labels=period_labels)
    wide = wide.rename(index=_cf_row_labels(cfg))
    _render_table(wide)


def _per_kg_row_labels(cfg):
    """Radnavnene har IKKE lenger 'WFE'/'HOG' hardkodet - enheten står i
    kolonneoverskriften i stedet (f.eks. 'Totalt (WFE)' / 'Totalt (HOG)'),
    så samme radnavn brukes uansett hvilken kolonne man ser på."""
    labels = {"kohort_id": "Kohort", "batch_id": "Batch", "dato": "Dato", "fase": "Fase",
              "kg_wfe_brutto_ref": "Bruttovekst, grunnlag kostnadslinjer (kg)",
              "kg_levert_ref": "Levert biomasse (kunde), kg",
              "kg_netto_ref": "Netto tilvekst - levert minus innkjøpt (kg)",
              "inntekt_kr": "Inntekt (kr/kg)"}
    for r in cfg.RESOURCES:
        labels[f"kr_{r['id']}"] = f"{r['navn']} (kr/kg)"
    labels.update({
        "kostnad_totalt_kr": "Kostnad totalt (kr/kg)",
        "netto_kontantstrom_kr": "Netto kontantstrøm (kr/kg)",
        "akkumulert_kontantstrom_kr": "Akkumulert kontantstrøm (kr/kg)",
    })
    return labels


def _build_per_kg_wide(df, period_col, period_labels=None,
                        revenue_denom_col="kg_wfe_levert", cum_denom_col="kg_wfe_levert_akkumulert",
                        cost_hog_faktor=1.0, apply_labels=True):
    per_kg = build_per_kg(df, revenue_denom_col=revenue_denom_col, cum_denom_col=cum_denom_col,
                           cost_hog_faktor=cost_hog_faktor)

    # TRE referanserader settes tilbake inn ØVERST, UDELT (i kg, ikke kr/kg):
    #   1) Bruttovekst - nevneren bak kostnadslinjene (x cost_hog_faktor,
    #      slik at HOG-kolonnen viser SIN egen faktiske nevner, ikke samme
    #      rå WFE-tall som WFE-kolonnen).
    #   2) Levert biomasse (kunde) - nevneren bak inntekt/netto/akkumulert
    #      (revenue_denom_col er allerede riktig valgt av kalleren: ren
    #      kg_wfe_levert for WFE-kolonnen, kg_solgt for HOG-kolonnen).
    #   3) Netto tilvekst (levert MINUS innkjøpt smoltbiomasse) - samme
    #      skalering som bruttovekst (x cost_hog_faktor), slik at man ser
    #      levert, netto tilvekst OG brutto (kostnadsrelevant) side om side.
    ref_cols = []
    if "kg_wfe_brutto" in df.columns:
        per_kg.insert(0, "kg_wfe_brutto_ref", df["kg_wfe_brutto"].to_numpy() * cost_hog_faktor)
        ref_cols.append("kg_wfe_brutto_ref")
    if revenue_denom_col in df.columns:
        per_kg.insert(1, "kg_levert_ref", df[revenue_denom_col].to_numpy())
        ref_cols.append("kg_levert_ref")
    if "kg_wfe_netto_akkumulert" in df.columns:
        per_kg.insert(2, "kg_netto_ref", df["kg_wfe_netto_akkumulert"].to_numpy() * cost_hog_faktor)
        ref_cols.append("kg_netto_ref")

    id_cols = ("kohort_id", "batch_id", "uke", "dato", "fase", "periode")
    kr_per_kg_cols = [c for c in per_kg.columns if c not in id_cols and c not in ref_cols]

    formatted = with_thousands(per_kg, int_cols=[], float_cols=kr_per_kg_cols, float_decimals=2)
    if ref_cols:
        formatted = with_thousands(formatted, int_cols=[], float_cols=ref_cols, float_decimals=0)
    for col in formatted.columns:
        formatted[col] = formatted[col].apply(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else v)

    if period_labels is not None:
        formatted[period_col] = period_labels
    wide = formatted.set_index(period_col).T
    wide.index.name = "Felt"
    # Radnavnene byttes til lesbare navn FØR retur (med mindre apply_labels=False,
    # brukt når denne tabellen skal kombineres med andre FØRST - da må radene
    # ha IDENTISK (rå) indeks på tvers av tabellene, ellers feiler pandas sin
    # indeks-justering ved sammenslåing (det var årsaken til at HOG-kolonnen
    # tidligere viste "nan" for kostnadsradene).
    if apply_labels:
        wide = wide.rename(index=_per_kg_row_labels(cfg))
    return wide


def _show_per_kg_table(df, period_col, period_labels=None):
    wide = _build_per_kg_wide(df, period_col, period_labels=period_labels)
    _render_table(wide)


def _fixed_costs_for_periode(fixed_costs_weekly, uker, period):
    """Filtrerer fixed_costs_weekly til AKKURAT de ukene en valgt batch/kohort
    dekker, og aggregerer til samme periodenivå (uke/maned/ar/totalt) som
    resten av visningen - slik at faste kostnader kan vises SIDE OM SIDE
    med batchens/kohortens egne tall, uten å prøve å fordele dem ned."""
    sub = fixed_costs_weekly[fixed_costs_weekly["uke"].isin(uker)].copy()
    if period == "uke":
        sub["periode"] = sub["uke"]
    elif period == "maned":
        sub["periode"] = pd.to_datetime(sub["dato"]).dt.strftime("%Y-%m")
    elif period == "ar":
        sub["periode"] = pd.to_datetime(sub["dato"]).dt.isocalendar().year
    else:
        sub["periode"] = "Totalt"
    kr_cols = [c for c in sub.columns if c.startswith("kr_")]
    return sub.groupby("periode", sort=True)[kr_cols].sum().reset_index()


def _show_kontantstrom_kombinert(df, period_col, period_labels=None, hog_faktor=1.0, fixed_costs_df=None):
    """Viser (kr), (kr/kg WFE) og (kr/kg HOG) SIDE VED SIDE per periode -
    slår sammen det som før var to separate tabeller (Kontantstrøm +
    "... per kg") til én, for å spare plass og gjøre det lettere å
    sammenligne totalt/WFE/HOG i ett blikk.

    Kombineres på RÅ (ikke omdøpte) kolonnenavn først - deretter gis ÉN
    felles, enhetsnøytral radetikett til slutt. Renner man de tre
    delrutene med HVER SIN etikett-sett før sammenslåing, feiler pandas
    sin indeks-justering (samme bug som ble rettet i forrige runde).

    `fixed_costs_df`: valgfri, forhåndsaggregert tabell fra
    _fixed_costs_for_periode() - viser de faste kostnadene (13-16, inkl.
    underlinjer) SOM DE ER for periodene (FULL verdi, IKKE fordelt/redusert
    ned til én batch/kohort sin andel) - kun i (kr)-kolonnen, siden faste
    kostnader ikke er priset per kg WFE/HOG. Brukes i Batch-/Kohortoversikt,
    der '13.5 Desinfeksjon' er et godt eksempel på en 'semi-variabel' post
    (avhenger av antall kohorter i perioden, ikke av hvilken batch man ser
    på isolert)."""
    wide_kr_raw = _transpose_for_display(df, period_col, period_labels=period_labels)
    wide_wfe_raw = _build_per_kg_wide(df, period_col, period_labels=period_labels,
                                       revenue_denom_col="kg_wfe_levert", cum_denom_col="kg_wfe_levert_akkumulert",
                                       cost_hog_faktor=1.0, apply_labels=False)
    wide_hog_raw = _build_per_kg_wide(df, period_col, period_labels=period_labels,
                                       revenue_denom_col="kg_solgt", cum_denom_col="kg_solgt_akkumulert",
                                       cost_hog_faktor=hog_faktor, apply_labels=False)

    # wide_kr_raw sine referanserader heter "kg_wfe_brutto"/"kg_wfe_levert"/
    # "kg_wfe_netto_akkumulert" - gi dem samme radnavn som per-kg-tabellene
    # ("..._ref") FØR sammenslåing, slik at radene faktisk matcher på tvers
    # av de tre kildetabellene.
    wide_kr_raw = wide_kr_raw.rename(index={
        "kg_wfe_brutto": "kg_wfe_brutto_ref", "kg_wfe_levert": "kg_levert_ref",
        "kg_wfe_netto_akkumulert": "kg_netto_ref",
    })

    rekkefolge = (["inntekt_kr"] + [f"kr_{r['id']}" for r in cfg.RESOURCES] +
                  ["kostnad_totalt_kr", "kg_wfe_brutto_ref", "kg_levert_ref", "kg_netto_ref",
                   "netto_kontantstrom_kr", "akkumulert_kontantstrom_kr"])

    perioder = list(wide_wfe_raw.columns)
    combined = pd.DataFrame(index=rekkefolge)
    for p in perioder:
        combined[f"{p} (kr)"] = wide_kr_raw[p].reindex(rekkefolge)
        combined[f"{p} (kr/kg WFE)"] = wide_wfe_raw[p].reindex(rekkefolge)
        combined[f"{p} (kr/kg HOG)"] = wide_hog_raw[p].reindex(rekkefolge)

    # Dekningsbidrag/-grad - ISOLERT til akkurat denne batchen/kohorten/
    # perioden (df kan være filtrert til én batch, se kalleren) - beregnes
    # direkte fra df sine rå tall, ikke fra de allerede formaterte
    # wide_wfe_raw/wide_hog_raw-tabellene (som inneholder STRENGER, ikke
    # tall). Dekningsgrad (%) er en RATIO og blir identisk uansett WFE/HOG/
    # kr-grunnlag (samme tall i alle tre kolonner)."""
    if "inntekt_kr" in df.columns and "kostnad_totalt_kr" in df.columns:
        dekningsbidrag_kr = df["inntekt_kr"].astype(float) - df["kostnad_totalt_kr"].astype(float)
        dekningsgrad_pct = (dekningsbidrag_kr / df["inntekt_kr"].astype(float) * 100).where(df["inntekt_kr"] > 0)
        dekningsbidrag_wfe = dekningsbidrag_kr / df["kg_wfe_levert"].replace(0, pd.NA) if "kg_wfe_levert" in df.columns else None
        dekningsbidrag_hog = dekningsbidrag_kr / df["kg_solgt"].replace(0, pd.NA) if "kg_solgt" in df.columns else None

        db_row = pd.Series(index=combined.columns, dtype=object)
        dg_row = pd.Series(index=combined.columns, dtype=object)
        for i, p in enumerate(perioder):
            db_kr = dekningsbidrag_kr.iloc[i]
            db_row[f"{p} (kr)"] = fmt_int(db_kr) if pd.notna(db_kr) else ""
            db_row[f"{p} (kr/kg WFE)"] = fmt_float(dekningsbidrag_wfe.iloc[i], 2) if dekningsbidrag_wfe is not None and pd.notna(dekningsbidrag_wfe.iloc[i]) else ""
            db_row[f"{p} (kr/kg HOG)"] = fmt_float(dekningsbidrag_hog.iloc[i], 2) if dekningsbidrag_hog is not None and pd.notna(dekningsbidrag_hog.iloc[i]) else ""
            dg = dekningsgrad_pct.iloc[i]
            dg_verdi = f"{fmt_float(dg, 1)} %" if pd.notna(dg) else ""
            dg_row[f"{p} (kr)"] = dg_verdi
            dg_row[f"{p} (kr/kg WFE)"] = dg_verdi
            dg_row[f"{p} (kr/kg HOG)"] = dg_verdi
        combined.loc["dekningsbidrag_rad"] = db_row
        combined.loc["dekningsgrad_rad"] = dg_row

    # FCR (fôrforbruk / bruttovekst) - samme "grunnlag"-prinsipp som
    # kostnadslinjene: WFE-kolonnen bruker ren bruttovekst, HOG-kolonnen
    # bruker bruttovekst x HOG-faktor - gir naturlig en HØYERE (dårligere)
    # FCR i HOG-kolonnen, siden samme fôrmengde da måles mot færre
    # sellbare kg. Regnes direkte her (ikke via build_per_kg, siden FCR
    # ikke er et kr-tall)."""
    if "mengde_for" in df.columns and "kg_wfe_brutto" in df.columns:
        fcr_wfe = df["mengde_for"].astype(float) / df["kg_wfe_brutto"].replace(0, pd.NA)
        fcr_hog = fcr_wfe / hog_faktor
        fcr_row = pd.Series(index=combined.columns, dtype=object)
        for i, p in enumerate(perioder):
            fcr_row[f"{p} (kr)"] = ""
            fcr_row[f"{p} (kr/kg WFE)"] = fmt_float(fcr_wfe.iloc[i], 2) if pd.notna(fcr_wfe.iloc[i]) else ""
            fcr_row[f"{p} (kr/kg HOG)"] = fmt_float(fcr_hog.iloc[i], 2) if pd.notna(fcr_hog.iloc[i]) else ""
        combined.loc["fcr_rad"] = fcr_row

    fastkost_navn_map = {}
    if fixed_costs_df is not None and len(fixed_costs_df) > 0:
        fastkost_rekkefolge = (["kr_leie_anlegg"] + [f"kr_{sl['id']}" for sl in cfg.HEXACAGE_LEIE_SUBLINJER] +
                                [f"kr_{fc['id']}" for fc in cfg.FIXED_COSTS if fc["id"] != "leie_anlegg"] +
                                ["kr_faste_totalt"])
        fastkost_navn_map = {"kr_leie_anlegg": "13. Leie av Big Dipper-anlegg (fast, uendret)"}
        for sl in cfg.HEXACAGE_LEIE_SUBLINJER:
            fastkost_navn_map[f"kr_{sl['id']}"] = f"{sl['navn']} (fast, uendret)"
        for fc in cfg.FIXED_COSTS:
            if fc["id"] != "leie_anlegg":
                fastkost_navn_map[f"kr_{fc['id']}"] = f"{fc['navn']} (fast, uendret)"
        fastkost_navn_map["kr_faste_totalt"] = "Faste kostnader totalt (fast, uendret)"

        fk_wide = fixed_costs_df.set_index("periode")[fastkost_rekkefolge].T
        for r in fastkost_rekkefolge:
            rad = pd.Series(index=combined.columns, dtype=object)
            for p in perioder:
                verdi = fk_wide.loc[r, p] if p in fk_wide.columns else 0.0
                rad[f"{p} (kr)"] = fmt_int(verdi) if pd.notna(verdi) else ""
                rad[f"{p} (kr/kg WFE)"] = ""
                rad[f"{p} (kr/kg HOG)"] = ""
            combined.loc[r] = rad

    combined.index.name = "Felt"
    combined = combined.rename(index={
        **_per_kg_row_labels(cfg), "fcr_rad": "FCR (fôr / bruttovekst)",
        "dekningsbidrag_rad": "Dekningsbidrag", "dekningsgrad_rad": "Dekningsgrad",
        **fastkost_navn_map,
    })
    combined = combined.fillna("")
    _render_table(combined)


visning = st.selectbox(
    "Vis:", options=["Ukeoversikt", "Månedsoversikt", "Årsoversikt", "Kohortoversikt", "Batchoversikt"], index=4,
    key="kontantstrom_visning",
)

fixed_costs_args = None  # settes kun for Kohort-/Batchoversikt

if visning == "Ukeoversikt":
    cf_year_options = sorted(pd.to_datetime(cashflow["dato"]).dt.isocalendar().year.unique())
    cf_yr_pick = st.selectbox("Vis uker i år:", options=["Alle år"] + list(cf_year_options), key="cashflow_year")
    cf_show = (cashflow if cf_yr_pick == "Alle år"
               else cashflow[pd.to_datetime(cashflow["dato"]).dt.isocalendar().year == cf_yr_pick])
    if n_tanker_aktiv > 1:
        # Flere tanker i samme uke -> summert per uke (hele anlegget)
        cf_show = summarize_cashflow_by_period(cf_show, "uke")
        per_kg_args = (cf_show, "periode", None)
    else:
        per_kg_args = (cf_show, "uke", None)

elif visning == "Månedsoversikt":
    cf_maned = summarize_cashflow_by_period(cashflow, "maned")
    cf_maned_labels = cf_maned["periode"].apply(month_label)
    per_kg_args = (cf_maned, "periode", cf_maned_labels)

elif visning == "Årsoversikt":
    cf_ar = summarize_cashflow_by_period(cashflow, "ar")
    per_kg_args = (cf_ar, "periode", None)

elif visning == "Kohortoversikt":
    _kohort_i_cf = {k for k in cashflow["kohort_id"] if not str(k).startswith("(")}
    kohort_ids = [k for k in generations if k in _kohort_i_cf]  # kronologisk, ikke alfabetisk (K1, K10, K2 ...)
    valgt_kohort = st.selectbox("Velg kohort:", options=kohort_ids, key="cashflow_kohort")
    kohort_show = cashflow[cashflow["kohort_id"].isin([valgt_kohort, f"({valgt_kohort})"])]
    kohort_batcher = sorted({b for b in kohort_show["batch_id"] if not str(b).startswith("(")})
    st.caption(f"{valgt_kohort} samlet - alle batcher slått sammen: {', '.join(kohort_batcher)}.")

    kohort_granularitet = st.selectbox(
        "Vis kohorten som:", options=["Uke", "Måned", "År", "Totalt"], index=3,
        key="cashflow_kohort_granularitet",
    )
    if kohort_granularitet == "Uke":
        per_kg_args = (kohort_show, "uke", None)
    elif kohort_granularitet == "Måned":
        kohort_maned = summarize_cashflow_by_period(kohort_show, "maned")
        kohort_maned_labels = kohort_maned["periode"].apply(month_label)
        per_kg_args = (kohort_maned, "periode", kohort_maned_labels)
    elif kohort_granularitet == "År":
        kohort_ar = summarize_cashflow_by_period(kohort_show, "ar")
        per_kg_args = (kohort_ar, "periode", None)
    else:
        kohort_total = summarize_cashflow_by_period(kohort_show, "totalt")
        per_kg_args = (kohort_total, "periode", None)

else:  # Batchoversikt
    _batch_i_cf = {b for b in cashflow["batch_id"] if not str(b).startswith("(")}
    batch_ids = [b["batch_id"] for info in generations.values() for b in info["batches"] if b["batch_id"] in _batch_i_cf]
    # Default til "K1-B8" (siste batch i første kohort, typisk den mest
    # interessante å se på først) hvis den finnes i denne kjøringen - faller
    # tilbake til første batch i listen ellers (f.eks. færre batcher/uker
    # per kohort enn 8).
    _forste_kohort_id = next(iter(generations), "K1")
    _default_batch = f"{_forste_kohort_id}-B{len(generations[_forste_kohort_id]['batches'])}" if generations else "K1-B8"
    _batch_default_idx = batch_ids.index(_default_batch) if _default_batch in batch_ids else 0
    valgt_batch = st.selectbox("Velg batch:", options=batch_ids, index=_batch_default_idx, key="cashflow_batch")
    kohort_for_batch = valgt_batch.split("-B")[0]
    batch_show = cashflow[cashflow["batch_id"].isin([valgt_batch, f"({valgt_batch})"])]

    batch_granularitet = st.selectbox(
        "Vis batchen som:", options=["Uke", "Måned", "År", "Totalt"], index=0,
        key="cashflow_batch_granularitet",
    )

    if batch_granularitet == "Uke":
        st.caption(f"Viser {valgt_batch} sin egen ukentlige PnL/kontantstrøm, fra innsett til levering.")
        per_kg_args = (batch_show, "uke", None)
    elif batch_granularitet == "Måned":
        st.caption(f"Viser {valgt_batch} sin PnL/kontantstrøm summert per måned.")
        batch_maned = summarize_cashflow_by_period(batch_show, "maned")
        batch_maned_labels = batch_maned["periode"].apply(month_label)
        per_kg_args = (batch_maned, "periode", batch_maned_labels)
    elif batch_granularitet == "År":
        st.caption(f"Viser {valgt_batch} sin PnL/kontantstrøm summert per år.")
        batch_ar = summarize_cashflow_by_period(batch_show, "ar")
        per_kg_args = (batch_ar, "periode", None)
    else:  # Totalt
        st.caption(f"Viser {valgt_batch} sin PnL/kontantstrøm summert over hele levetiden (én kolonne).")
        batch_total = summarize_cashflow_by_period(batch_show, "totalt")
        per_kg_args = (batch_total, "periode", None)



if visning == "Batchoversikt":
    with st.expander(f"📈 Isolert utvikling {min(eskalering_ar)}-{max(eskalering_ar)} for {valgt_batch} (hele driften = kun denne batchen, escalert normalt)", expanded=False):
        st.caption(
            f"Hypotetisk fremskrivning: hva om HELE anleggets produksjon, hvert år, besto av "
            f"NØYAKTIG {valgt_batch} sitt fysiske volum (samme kg fôr, samme kg WFE bruttovekst "
            "osv.), men priset med DET ÅRETS eskalerte satser? INGEN faste kostnader (13-16) er "
            "med her - de er anleggsnivå/semi-variable, ikke batch-nivå (samme prinsipp som "
            f"Resultatregnskap/Balanse per batch under). Batchens fysiske volum er hentet ved å "
            f"fordele kohorten ({kohort_for_batch}) sin TOTALE ressursbruk proporsjonalt etter "
            f"{valgt_batch} sin andel av kohortens leverte biomasse (siden vekst-/fôrhistorikken "
            "før salgsvinduet starter er delt mellom alle batchene i kohorten)."
        )
        isolert_proj, isolert_andel = build_isolert_batch_projeksjon(
            cfg, ledger, generations, fixed_costs_weekly, kohort_for_batch, valgt_batch,
            eskalering_ar, sales_price_kr_per_kg=cfg.SALES_PRICE_KR_PER_KG, hog_faktor=cfg.HOG_FAKTOR,
            seasonal_index_by_week=seasonal_index_runtime, sales_price_table=_sales_price_table_runtime,
        )
        st.caption(f"{valgt_batch} sin andel av kohorten {kohort_for_batch}: {isolert_andel*100:.1f} % av leveransen.")

        isolert_row_labels = {"periode": "År", "inntekt_kr": "Inntekt (kr)"}
        for r in cfg.RESOURCES:
            isolert_row_labels[f"kr_{r['id']}"] = f"{r['navn']} (kr)"
        isolert_row_labels["kostnad_variabel_totalt_kr"] = "Variabel kostnad totalt (kr)"
        isolert_row_labels["dekningsbidrag_kr"] = "Dekningsbidrag (kr)"
        isolert_row_labels["dekningsgrad_pct"] = "Dekningsgrad (%)"
        isolert_row_labels["akkumulert_dekningsbidrag_kr"] = "Akkumulert dekningsbidrag (kr)"
        isolert_per_kg = [c for c in isolert_proj.columns if c.endswith("__per_kg_solgt")]
        for kol in isolert_per_kg:
            mor_kol = kol.removesuffix("__per_kg_solgt")
            mor_navn = isolert_row_labels.get(mor_kol, mor_kol).removesuffix(" (kr)")
            isolert_row_labels[kol] = f"{mor_navn} per solgt kg ({solgt_enhet})"
        isolert_per_kg_navn = [isolert_row_labels[k] for k in isolert_per_kg]

        isolert_int_like = [c for c in isolert_proj.columns if c not in ("periode",)
                             and c not in ("dekningsgrad_pct",) and c not in isolert_per_kg]
        isolert_formatted = with_thousands(isolert_proj, int_cols=[], float_cols=isolert_int_like, float_decimals=0)
        isolert_formatted = with_thousands(isolert_formatted, int_cols=[], float_cols=["dekningsgrad_pct"], float_decimals=1)
        isolert_formatted = with_thousands(isolert_formatted, int_cols=[], float_cols=isolert_per_kg, float_decimals=2)
        for col in isolert_formatted.columns:
            isolert_formatted[col] = isolert_formatted[col].apply(
                lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else v
            )
        isolert_wide = isolert_formatted.set_index("periode").T
        isolert_wide.index.name = "Felt"
        isolert_wide = isolert_wide.rename(index=isolert_row_labels)
        _render_table(isolert_wide, highlight_groups=[
            {"rows": isolert_per_kg_navn, "bg": "#fbf3e6", "text": "#9a6b2a"},
        ])

    with st.expander(f"📄 Resultatregnskap og Balanse - {valgt_batch} isolert (ekskl. faste kostnader)", expanded=False):
        st.caption(
            f"Egen, isolert PnL og Balanse for AKKURAT {valgt_batch} - INGEN faste kostnader "
            "(13-16) inkludert, siden disse er anleggsnivå/semi-variable, ikke batch-nivå "
            "(f.eks. avhenger '13.5 Desinfeksjon' av antall kohorter i perioden, ikke av "
            f"hvilken batch man ser på). Kostnadene (0-12) er {valgt_batch} sin andel av "
            f"kohorten ({kohort_for_batch}) sin ukentlige kostnad GJENNOM HELE VEKSTPERIODEN "
            "(ikke bare i leveringsuken), slik at batchens EGEN biologiske eiendel bygger seg "
            "gradvis opp under vekst og tømmes helt i akkurat DENNE batchens leveringsuke."
        )
        batch_resultat, batch_balanse, batch_kontantstrom = build_batch_resultat_og_balanse(
            cfg, ledger, cashflow, generations, kohort_for_batch, valgt_batch,
            cfg.START_ISO_YEAR, cfg.START_ISO_WEEK, kundefrist_uker=2, leverandorfrist_uker=1,
        )

        st.markdown("**Resultatregnskap**")
        batch_resultat_row_labels = {
            "uke": "Uke", "inntekt_kr": "Driftsinntekter (kr)", "varekostnad_kr": "Varekostnad (kr)",
            "bruttofortjeneste_kr": "Bruttofortjeneste (kr)", "lonnskostnader_kr": "Lønnskostnader (kr)",
            "arsresultat_for_skatt_kr": "Årsresultat før skatt (kr)",
        }
        batch_resultat_fmt = with_thousands(
            batch_resultat.drop(columns=["dato"]), int_cols=[],
            float_cols=[c for c in batch_resultat.columns if c not in ("uke", "dato")], float_decimals=0,
        )
        batch_resultat_wide = batch_resultat_fmt.set_index("uke").T
        batch_resultat_wide.index.name = "Felt"
        batch_resultat_wide = batch_resultat_wide.rename(index=batch_resultat_row_labels)
        BATCH_FELT_BREDDE_PX = 320  # delt bredde for Resultatregnskap/Kontantstrøm/Balanse - se under
        _render_table(batch_resultat_wide, fixed_label_width_px=BATCH_FELT_BREDDE_PX, highlight_groups=[
            {"rows": ["Bruttofortjeneste (kr)", "Årsresultat før skatt (kr)"], "bg": "#eef7ee", "text": "#3d7a3d"},
        ])

        # ---- Headline: operasjonell kapital-behov for DENNE batchen ----
        batch_bunn_idx = batch_kontantstrom["akkumulert_kontantstrom_kr"].idxmin()
        batch_bunn_verdi = batch_kontantstrom.loc[batch_bunn_idx, "akkumulert_kontantstrom_kr"]
        batch_bunn_uke = batch_kontantstrom.loc[batch_bunn_idx, "uke"]
        batch_bunn_dato = pd.to_datetime(batch_kontantstrom.loc[batch_bunn_idx, "dato"])
        batch_sluttresultat = batch_kontantstrom["akkumulert_kontantstrom_kr"].iloc[-1]
        batch_forhold_pct = (batch_sluttresultat / abs(batch_bunn_verdi) * 100) if batch_bunn_verdi != 0 else None

        hc1, hc2, hc3, hc4 = st.columns(4)
        hc1.metric(
            f"Operasjonell kapital {valgt_batch} krever",
            f"{fmt_int(abs(batch_bunn_verdi))} kr",
            help="Det største akkumulerte kontantunderskuddet gjennom batchens levetid - hvor "
                 "mye kapital som må være tilgjengelig for å dekke denne ene batchen helt til "
                 "den leveres og genererer inntekt.",
        )
        hc2.metric(
            "Peaker i",
            f"{batch_bunn_uke} ({batch_bunn_dato.strftime('%b %Y')})",
            help="Uken/måneden der batchens akkumulerte kontantstrøm når sitt laveste punkt.",
        )
        hc3.metric(
            "Netto kontantstrøm / operasjonell kapital",
            f"{fmt_float(batch_forhold_pct, 0)} %" if batch_forhold_pct is not None else "–",
            help="Batchens totale netto kontantstrøm ved levetidens slutt, delt på den "
                 "operasjonelle kapitalen den krevde på det verste - et mål på avkastning "
                 "relativt til kapitalen som var bundet opp.",
        )
        hc4.metric(
            "Akkumulert netto kontantstrøm mottatt",
            f"{fmt_int(batch_sluttresultat)} kr",
            help="Batchens akkumulerte netto kontantstrøm slik den står ved siste uke - det "
                 "oppdretter faktisk sitter igjen med i kontanter fra denne batchen alene, "
                 "etter at alle betalinger og innbetalinger for batchen er gjort opp.",
        )

        st.markdown("**Kontantstrøm**")
        st.caption(
            "Viser den FAKTISKE kontantbevegelsen, MED betalingsutsettelse (kunder betaler 2 "
            "uker etter levering, leverandører betales 1 uke etter at kostnaden påløper) - til "
            "forskjell fra Resultatregnskapet over, som bruker MATCHET kostnad (alt bokføres i "
            "leveringsuken). Hver kostnadslinje (0-12) er spesifisert BÅDE når den påløper OG "
            "når den faktisk betales, slik at hele bildet kan gås gjennom linje for linje. "
            "'Akkumulert kontantstrøm' stemmer nøyaktig med 'Kontanter' i Balansen under."
        )
        batch_kontantstrom_row_labels = {"uke": "Uke", "palopt_inntekt_kr": "Påløpt inntekt (kr)"}
        for r in cfg.RESOURCES:
            batch_kontantstrom_row_labels[f"palopt_{r['id']}_kr"] = f"Påløpt - {r['navn']} (kr)"
        batch_kontantstrom_row_labels["palopt_kostnad_totalt_kr"] = "Påløpt kostnad totalt (kr)"
        batch_kontantstrom_row_labels["innbetalt_kr"] = "Innbetalt fra kunder (kr)"
        for r in cfg.RESOURCES:
            batch_kontantstrom_row_labels[f"utbetalt_{r['id']}_kr"] = f"Utbetalt - {r['navn']} (kr)"
        batch_kontantstrom_row_labels["utbetalt_kostnad_totalt_kr"] = "Utbetalt til leverandører totalt (kr)"
        batch_kontantstrom_row_labels["netto_kontantstrom_kr"] = "Netto kontantstrøm (kr)"
        batch_kontantstrom_row_labels["akkumulert_kontantstrom_kr"] = "Akkumulert kontantstrøm (kr)"

        batch_kontantstrom_fmt = with_thousands(
            batch_kontantstrom.drop(columns=["dato"]), int_cols=[],
            float_cols=[c for c in batch_kontantstrom.columns if c not in ("uke", "dato")], float_decimals=0,
        )
        batch_kontantstrom_wide = batch_kontantstrom_fmt.set_index("uke").T
        batch_kontantstrom_wide.index.name = "Felt"
        batch_kontantstrom_wide = batch_kontantstrom_wide.rename(index=batch_kontantstrom_row_labels)
        batch_kontantstrom_palopt_linjer = [f"Påløpt - {r['navn']} (kr)" for r in cfg.RESOURCES]
        batch_kontantstrom_utbetalt_linjer = [f"Utbetalt - {r['navn']} (kr)" for r in cfg.RESOURCES]
        _render_table(batch_kontantstrom_wide, fixed_label_width_px=BATCH_FELT_BREDDE_PX, highlight_groups=[
            {"rows": batch_kontantstrom_palopt_linjer, "bg": "#fbf3e6", "text": "#9a6b2a"},
            {"rows": batch_kontantstrom_utbetalt_linjer, "bg": "#eef1f6", "text": "#5b6b82"},
            {"rows": ["Netto kontantstrøm (kr)", "Akkumulert kontantstrøm (kr)"], "bg": "#eef7ee", "text": "#3d7a3d"},
        ])

        st.markdown("**Balanse**")
        batch_balanse_row_labels = {
            "uke": "Uke", "kontanter": "Kontanter (kr)", "kundefordringer": "Kundefordringer (kr)",
            "biologisk_eiendel": "Biologisk eiendel (kr)", "sum_eiendeler": "Sum eiendeler (kr)",
            "leverandorgjeld": "Leverandørgjeld (kr)", "opptjent_egenkapital": "Opptjent egenkapital (kr)",
            "sum_gjeld_og_egenkapital": "Sum gjeld og egenkapital (kr)", "differanse": "Differanse (skal være 0)",
        }
        batch_balanse_fmt = with_thousands(
            batch_balanse.drop(columns=["dato"]), int_cols=[],
            float_cols=[c for c in batch_balanse.columns if c not in ("uke", "dato")], float_decimals=0,
        )
        batch_balanse_wide = batch_balanse_fmt.set_index("uke").T
        batch_balanse_wide.index.name = "Felt"
        batch_balanse_wide = batch_balanse_wide.rename(index=batch_balanse_row_labels)
        _render_table(batch_balanse_wide, fixed_label_width_px=BATCH_FELT_BREDDE_PX, highlight_groups=[
            {"rows": ["Sum eiendeler (kr)", "Sum gjeld og egenkapital (kr)"], "bg": "#eef7ee", "text": "#3d7a3d"},
            {"rows": ["Differanse (skal være 0)"], "bg": "#fdeaea", "text": "#a33a3a"},
        ])

# ----------------------------------------------------------------------
# KONSOLIDERT KONTANTSTRØM - hele anlegget, summert på tvers av ALLE
# batcher/kohorter, PLUSS de faste kostnadene (13-16) som ikke kan
# tilordnes én enkelt batch. Akkumulert kontantstrøm er her en LØPENDE SUM
# over hele kalendertiden (nullstilles IKKE per kohort, i motsetning til
# batch-visningene over) - det er nettopp poenget med "konsolidert".
# ----------------------------------------------------------------------
with st.expander("13.2 Oppankring - nedbetalingsplan (renter og avdrag per måned)", expanded=False):
    st.caption(
        "Dette er utleiers (Aqualoop, SFaaS-leverandørens) EGET lån, ikke oppdretterens - vist her kun "
        "for innsyn i hvordan terminbeløpet oppdretter betaler er bygget opp. For oppdretter "
        "(Norway Offshore Salmon) er HELE terminbeløpet ren opex, uten renter/avdrag-splitt eller "
        "lånesaldo i egne oppstillinger (se Resultatregnskap og Balanse lenger ned)."
    )
    st.caption(
        f"Annuitetslån: {fmt_int(oppankring_inv)} kr, {oppankring_rente*100:.1f} % årsrente, "
        f"{int(oppankring_mnd)} måneders nedbetaling. Terminbeløpet ({fmt_int(oppankring_mnd_belop)} kr/mnd) "
        "er likt hver måned, men fordelingen mellom renter og avdrag endrer seg underveis - "
        "mer renter i starten (høy saldo), mer avdrag mot slutten (lav saldo)."
    )
    st.caption(
        f"Betales fra {prosjekt_start_dato.strftime('%d.%m.%Y')} til "
        f"{oppankring_sluttdato.strftime('%d.%m.%Y')} ({int(oppankring_mnd)} mnd), deretter 0 - lånet er nedbetalt."
    )
    plan = annuitetsplan(oppankring_inv, oppankring_rente, int(oppankring_mnd))
    plan_display = plan.copy()
    for kol in ["inngaende_saldo", "renter", "avdrag", "terminbelop", "utgaende_saldo"]:
        plan_display[kol] = plan_display[kol].apply(lambda x: fmt_int(x))
    plan_display = plan_display.rename(columns={
        "maned": "Måned", "inngaende_saldo": "Inngående saldo (kr)", "renter": "Renter (kr)",
        "avdrag": "Avdrag (kr)", "terminbelop": "Terminbeløp (kr)", "utgaende_saldo": "Utgående saldo (kr)",
    })
    st.dataframe(plan_display, hide_index=True, use_container_width=True, height=300)
    c1, c2 = st.columns(2)
    c1.metric("Sum renter over hele løpetiden", f"{fmt_int(plan['renter'].sum())} kr")
    c2.metric("Sum avdrag over hele løpetiden", f"{fmt_int(plan['avdrag'].sum())} kr")

st.subheader("Konsolidert kontantstrøm")
st.caption(
    "Summert på tvers av ALLE batcher (ikke filtrert til én), pluss de faste kostnadene "
    "13-16 fra sidepanelet - disse er uavhengige av batch, men høyst reelle for anlegget "
    "som helhet. Akkumulert kontantstrøm er en løpende sum over hele kalendertiden, "
    "nullstilles aldri. Inntekts- og kostnadsindeks: første periode med reelt tall = 100."
)

if not any(v for v in cfg.FIXED_COST_KR_PER_UKE.values()):
    st.caption("Ingen faste kostnader satt i sidepanelet ennå (under 'Faste kostnader') - telles som 0 inntil videre.")

# ---- Operasjonell kapital-behov - alltid beregnet PÅ UKEBASIS (uavhengig av
# hvilken visning som er valgt under), slik at den faktiske ukentlige bunnen
# fanges opp presist - en måneds- eller årsvisning kunne skjult det verste
# punktet midt i perioden. ----
kons_uke_for_kapitalbehov = build_konsolidert_kontantstrom(cfg, cashflow, fixed_costs_weekly, "uke")
bunn_idx = kons_uke_for_kapitalbehov["akkumulert_kontantstrom_konsolidert_kr"].idxmin()
bunn_verdi = kons_uke_for_kapitalbehov.loc[bunn_idx, "akkumulert_kontantstrom_konsolidert_kr"]
bunn_uke = kons_uke_for_kapitalbehov.loc[bunn_idx, "periode"]
bunn_dato = pd.to_datetime(fixed_costs_weekly.set_index("uke").reindex([bunn_uke])["dato"].iloc[0])
c1, c2 = st.columns(2)
c1.metric(
    "Operasjonell kapital oppdretter må ha tilgjengelig",
    f"{fmt_int(abs(bunn_verdi))} kr",
    help="Det STØRSTE akkumulerte kontantunderskuddet gjennom hele simuleringen - altså hvor mye "
         "kapital som må være tilgjengelig for å dekke driften helt til kontantstrømmen snur og "
         "akkumulert saldo begynner å bli positiv for godt. Beregnet på ukebasis for presisjon.",
)
c2.metric(
    "Inntreffer i uke",
    f"{bunn_uke} ({bunn_dato.strftime('%b %Y')})",
    help="Uken der akkumulert kontantstrøm (konsolidert) når sitt laveste punkt.",
)

kons_visning = st.selectbox(
    "Vis konsolidert som:", options=["Ukeoversikt", "Månedsoversikt", "Årsoversikt"], index=2,
    key="konsolidert_visning",
)
kons_period = {"Ukeoversikt": "uke", "Månedsoversikt": "maned", "Årsoversikt": "ar"}[kons_visning]
kons = build_konsolidert_kontantstrom(cfg, cashflow, fixed_costs_weekly, kons_period)
kons_periode_raw_order = kons["periode"].tolist()  # RÅ periode-rekkefølge, FØR ev. pynting til
                                                     # visningslabels (se kons_period_labels under) -
                                                     # brukes til å slå opp riktig kolonne i
                                                     # batch-nedtrekket (_render_expandable_kontantstrom).

kons_period_labels = kons["periode"].apply(month_label) if kons_period == "maned" else None
kons_row_labels = {"periode": "Periode", "inntekt_kr": "Inntekt (kr)", "inntektsindeks": "Inntektsindeks"}
for r in cfg.RESOURCES:
    kons_row_labels[f"kr_{r['id']}"] = f"{r['navn']} (kr)"
kons_row_labels["kostnad_variabel_totalt_kr"] = "Variabel kostnad totalt (kr)"
kons_row_labels["dekningsbidrag_kr"] = "Dekningsbidrag (kr)"
kons_row_labels["dekningsgrad_pct"] = "Dekningsgrad (%)"
for sl in cfg.HEXACAGE_LEIE_SUBLINJER:
    kons_row_labels[f"kr_{sl['id']}"] = f"{sl['navn']} (kr)"
for fc in cfg.FIXED_COSTS:
    kons_row_labels[f"kr_{fc['id']}"] = f"{fc['navn']} (kr)"
kons_row_labels.update({
    "kr_faste_totalt": "Faste kostnader totalt (kr)",
    "kostnad_totalt_konsolidert_kr": "Kostnad totalt, konsolidert (kr)",
    "kostnadsindeks": "Kostnadsindeks",
    "netto_kontantstrom_konsolidert_kr": "Netto kontantstrøm, konsolidert (kr)",
    "akkumulert_kontantstrom_konsolidert_kr": "Akkumulert kontantstrøm, konsolidert (kr)",
})

# Alle "<x>__per_kg_solgt"-kolonnene (bygget generisk i
# build_konsolidert_kontantstrom) får radnavnet til sin "mor"-rad + eget
# "per kg HOG/WFE"-tillegg - og en EGEN liste, slik at de kan fargelegges
# annerledes enn resten av tabellen (se _render_table sin highlight_groups).
per_kg_kolonner = [c for c in kons.columns if c.endswith("__per_kg_solgt")]
for kol in per_kg_kolonner:
    mor_kol = kol.removesuffix("__per_kg_solgt")
    mor_navn = kons_row_labels.get(mor_kol, mor_kol).removesuffix(" (kr)")
    kons_row_labels[kol] = f"{mor_navn} per solgt kg ({solgt_enhet})"
per_kg_radnavn = [kons_row_labels[k] for k in per_kg_kolonner]

kons_id_cols = ("periode",)
kons_int_like = [c for c in kons.columns if c not in kons_id_cols and not c.endswith("indeks")
                 and c not in ("dekningsgrad_pct",) and c not in per_kg_kolonner]
kons_idx_cols = [c for c in kons.columns if c.endswith("indeks")]
kons_formatted = with_thousands(kons, int_cols=[], float_cols=kons_int_like, float_decimals=0)
kons_formatted = with_thousands(kons_formatted, int_cols=[], float_cols=kons_idx_cols, float_decimals=1)
kons_formatted = with_thousands(kons_formatted, int_cols=[], float_cols=["dekningsgrad_pct"], float_decimals=1)
kons_formatted = with_thousands(kons_formatted, int_cols=[], float_cols=per_kg_kolonner, float_decimals=2)
for col in kons_formatted.columns:
    kons_formatted[col] = kons_formatted[col].apply(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else v)
if kons_period_labels is not None:
    kons_formatted["periode"] = kons_period_labels
kons_wide = kons_formatted.set_index("periode").T
kons_wide.index.name = "Felt"
kons_wide = kons_wide.rename(index=kons_row_labels)
kons_sublinje_labels = [f"{sl['navn']} (kr)" for sl in cfg.HEXACAGE_LEIE_SUBLINJER]
# Felles, FAST bredde på "Felt"-kolonnen OG hver datakolonne (år/måned/uke) -
# delt med Resultatregnskap og Balanse lenger ned, slik at samme periode
# (f.eks. "2027") havner på nøyaktig samme horisontale posisjon i alle tre
# tabellene når de vises rett under hverandre. Uten dette får hver tabell
# sin egen kolonnebredde ut fra sitt eget innhold, og periodene sklir ut av
# vertikal linje med hverandre selv om tallene i seg selv stemmer.
KONSOLIDERT_FELT_BREDDE_PX = 340
KONSOLIDERT_KOL_BREDDE_PX = 120

# ---- Batch-nedtrekk (Excel-outline-stil) for Inntekt, hver ressurslinje
# 0-12, Variabel kostnad totalt og Dekningsbidrag - IKKE lenger enn det
# (per solgt kg/indeks-radene er forhold, ikke summer, og lar seg ikke
# meningsfullt dekomponere batch for batch). Bygges fra batch_ukentlig -
# SAMME kjernefunksjon (build_batch_ukentlig_kostnad()) som driver den
# matchede kostnaden i Resultatregnskap/Balanse lenger ned, slik at alle
# batch-nedtrekkene i hele appen er konsistente med hverandre. ----
batch_ukentlig = build_batch_ukentlig_kostnad(cfg, ledger, generations)
kons_kr_cost_cols = [f"kr_{r['id']}" for r in cfg.RESOURCES]
kons_breakdown = _build_kontantstrom_breakdown(cashflow, batch_ukentlig, kons_period, kons_kr_cost_cols)
kons_breakdown["kostnad_variabel_totalt_kr"] = kons_breakdown[kons_kr_cost_cols].sum(axis=1)
kons_breakdown["dekningsbidrag_kr"] = kons_breakdown["inntekt_kr"] - kons_breakdown["kostnad_variabel_totalt_kr"]

kons_expandable = {"Inntekt (kr)": "inntekt_kr"}
for r in cfg.RESOURCES:
    kons_expandable[f"{r['navn']} (kr)"] = f"kr_{r['id']}"
kons_expandable["Variabel kostnad totalt (kr)"] = "kostnad_variabel_totalt_kr"
kons_expandable["Dekningsbidrag (kr)"] = "dekningsbidrag_kr"

st.caption(
    "Klikk ▶ ved siden av Inntekt, en ressurslinje (0-12), Variabel kostnad totalt eller "
    "Dekningsbidrag for å se hvilke batcher tallet består av (Excel-outline-stil, kollapset "
    "som default). Inntekt er en ekte batch-hendelse (kommer inn i leveringsuken). "
    "Kostnadslinjene er PROPORSJONALT fordelt på alle batchene i kohorten - ukene før "
    "salgsvinduet er reelt delt mellom batchene (fisken sto i samme tank), ikke eid av én "
    "batch alene. '0. Kjøpt smolt' fordeles etter batchens andel av ANTALL fisk levert "
    "(smoltpris er kr/stk) - alle andre linjer fordeles etter andel av BIOMASSE levert "
    "(priset kr/kg WFE), siden batchene er like store i ANTALL men ulike i VEKT (senere "
    "batcher er tyngre)."
)
_render_expandable_kontantstrom(
    kons_wide, highlight_groups=[
        {"rows": kons_sublinje_labels, "bg": "#eef1f6", "text": "#5b6b82"},
        {"rows": per_kg_radnavn, "bg": "#fbf3e6", "text": "#9a6b2a"},
    ],
    felt_bredde_px=KONSOLIDERT_FELT_BREDDE_PX, kol_bredde_px=KONSOLIDERT_KOL_BREDDE_PX,
    expandable=kons_expandable, breakdown=kons_breakdown, periode_raw_order=kons_periode_raw_order,
)

# ----------------------------------------------------------------------
# RESULTATREGNSKAP - periodisert (påløpt), IKKE kontantstrøm. Eneste reelle
# forskjell fra Konsolidert kontantstrøm: "13.2 Oppankring" splittes i
# renter (finanskostnad, med i resultatet) og avdrag (kun balansen -
# IKKE bygget ennå). Foreløpig ingen skattekostnad - "Årsresultat før
# skatt" er dermed bunnlinjen inntil videre.
# ----------------------------------------------------------------------
st.subheader("Resultatregnskap")
st.caption(
    "Periodisert etter når inntekt/kostnad faktisk PÅLØPER (levering/forbruk), ikke når "
    "kontanter beveger seg - identisk med kontantstrømmen på inntekts- og variabel "
    "kostnadssiden i denne modellen (ingen betalingsutsettelse er lagt inn i selve "
    "resultatet - det kommer i balansen, med kundefordringer/leverandørgjeld). Eneste "
    "reelle forskjell fra 'Konsolidert kontantstrøm': '13.2 Oppankring' er splittet i "
    "renter (finanskostnad, med her) og avdrag (kun i balansen, se under). "
    "Under EBITDA: avskrivninger og finanskostnader (oppdretters egne, fra sidepanelet - "
    "anlegget eies av utleier og ligger i 13. Leie) og skatt med fremførbart underskudd. "
    "Disse tre er foreløpig KUN vist her - ikke bokført i kontantstrøm/balanse ennå."
)

resultat_visning = st.selectbox(
    "Vis resultatregnskap som:", options=["Ukeoversikt", "Månedsoversikt", "Årsoversikt"], index=2,
    key="resultat_visning",
)
resultat_period = {"Ukeoversikt": "uke", "Månedsoversikt": "maned", "Årsoversikt": "ar"}[resultat_visning]
matchet_kostnad = build_matchet_kostnad_per_uke(cfg, ledger, generations)
resultat = build_resultatregnskap(
    cfg, cashflow, fixed_costs_weekly, matchet_kostnad,
    cfg.START_ISO_YEAR, cfg.START_ISO_WEEK, resultat_period,
)

resultat_period_labels = resultat["periode"].apply(month_label) if resultat_period == "maned" else None
resultat_periode_raw_order = resultat["periode"].tolist()
resultat_row_labels = {
    "periode": "Periode",
    "kg_solgt": f"Solgt kg ({solgt_enhet})",
    "inntekt_kr": "Driftsinntekter (kr)",
    "varekostnad_kr": "Varekostnad (kr)",
    "bruttofortjeneste_kr": "Bruttofortjeneste (kr)",
    "lonnskostnader_kr": "Lønnskostnader (kr)",
    "andre_driftskostnader_kr": "Big Dipper leie, infrastrukturkostnader 13.1-13.10 (kr)",
    "ebitda_kr": "EBITDA (kr)",
    "avskrivninger_kr": "Avskrivninger (kr)",
    "ebit_kr": "EBIT (kr)",
    "finanskostnader_kr": "Finanskostnader (kr)",
    "resultat_for_skatt_kr": "Resultat før skatt (kr)",
    "skatt_kr": "Skatt (kr)",
    "resultat_etter_skatt_kr": "Resultat etter skatt (kr)",
}
# Ressurslinjene 0-12 (matchet) som underlinjer, + "per solgt kg"-rad under
# HVER rad (samme farge-konvensjon som Konsolidert kontantstrøm).
for r in cfg.RESOURCES:
    resultat_row_labels[f"kr_{r['id']}_matchet"] = f"{r['navn']} (kr)"
for sl in cfg.HEXACAGE_LEIE_SUBLINJER:          # 13.x - leie av Big Dipper, spesifisert
    resultat_row_labels[f"kr_{sl['id']}"] = f"{sl['navn']} (kr)"
for fc in cfg.FIXED_COSTS:                      # 14, 15, 16
    if fc["id"] != "leie_anlegg":
        resultat_row_labels[f"kr_{fc['id']}"] = f"{fc['navn']} (kr)"
resultat_kontroll_radnavn = ["Big Dipper leie, infrastrukturkostnader 13.1-13.10 (kr)"]
resultat_per_kg_kolonner = [c for c in resultat.columns if c.endswith("__per_kg_solgt")]
for kol in resultat_per_kg_kolonner:
    mor_kol = kol.removesuffix("__per_kg_solgt")
    mor_navn = resultat_row_labels.get(mor_kol, mor_kol).removesuffix(" (kr)")
    resultat_row_labels[kol] = f"{mor_navn} per solgt kg ({solgt_enhet})"
resultat_per_kg_radnavn = [resultat_row_labels[k] for k in resultat_per_kg_kolonner]
resultat_linje_radnavn = ([resultat_row_labels[f"kr_{r['id']}_matchet"] for r in cfg.RESOURCES]
                          + [resultat_row_labels[f"kr_{sl['id']}"] for sl in cfg.HEXACAGE_LEIE_SUBLINJER]
                          )
resultat_formatted = with_thousands(
    resultat, int_cols=[],
    float_cols=[c for c in resultat.columns if c != "periode" and c not in resultat_per_kg_kolonner], float_decimals=0,
)
resultat_formatted = with_thousands(resultat_formatted, int_cols=[], float_cols=resultat_per_kg_kolonner, float_decimals=2)
for col in resultat_formatted.columns:
    resultat_formatted[col] = resultat_formatted[col].apply(
        lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else v
    )
if resultat_period_labels is not None:
    resultat_formatted["periode"] = resultat_period_labels
resultat_wide = resultat_formatted.set_index("periode").T
resultat_wide.index.name = "Felt"
resultat_wide = resultat_wide.rename(index=resultat_row_labels)

# ---- Batch-nedtrekk for Driftsinntekter, Varekostnad, Bruttofortjeneste og
# Lønnskostnader - IKKE Andre driftskostnader eller Årsresultat (disse
# inkluderer faste kostnader 13-16, som er anleggsnivå, ikke batch-nivå). ----
resultat_breakdown = _build_resultatregnskap_breakdown(
    matchet_kostnad, cashflow, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK, resultat_period,
)
resultat_expandable = {
    "Driftsinntekter (kr)": "inntekt_kr",
    "Varekostnad (kr)": "varekostnad_kr",
    "Bruttofortjeneste (kr)": "bruttofortjeneste_kr",
    "Lønnskostnader (kr)": "lonnskostnader_kr",
}
st.caption(
    "Klikk ▶ ved siden av Driftsinntekter, Varekostnad, Bruttofortjeneste eller "
    "Lønnskostnader for å se hvilke batcher tallet består av. Ressurslinjene 0-12 (blågrå) er "
    "MATCHET kostnad per linje (bokført i leveringsuken, ikke spredt over vekstukene som i "
    "Konsolidert kontantstrøm) - 6 og 11 er oppdretters egne lønnskostnader. 'Big Dipper leie' (lilla) = HELE "
    "leien 13.1-13.10 (inkl. 13.7/13.8 som er utleiers folk fakturert i leien), påløpt per periode - lik "
    "'13. Leie av Big Dipper-anlegg' i Konsolidert kontantstrøm og leieinntekten hos utleier. 14. ADK og 15. "
    f"Administrasjon er oppdretters egne faste kostnader, egne linjer under leien. Gulbrune rader = samme tall "
    f"per solgt kg ({solgt_enhet}). Faste kostnader og resultatradene er IKKE brutt ned på batch (anleggsnivå)."
)
_render_expandable_kontantstrom(
    resultat_wide, highlight_groups=[
        {"rows": resultat_linje_radnavn, "bg": "#eef1f6", "text": "#5b6b82"},
        {"rows": resultat_per_kg_radnavn, "bg": "#fbf3e6", "text": "#9a6b2a"},
        {"rows": resultat_kontroll_radnavn, "bg": "#f3eef6", "text": "#6b4f82"},
        {"rows": ["Bruttofortjeneste (kr)", "EBITDA (kr)", "EBIT (kr)", "Resultat før skatt (kr)",
                  "Resultat etter skatt (kr)"], "bg": "#eef7ee", "text": "#3d7a3d"},
    ],
    felt_bredde_px=KONSOLIDERT_FELT_BREDDE_PX, kol_bredde_px=KONSOLIDERT_KOL_BREDDE_PX,
    expandable=resultat_expandable, breakdown=resultat_breakdown, periode_raw_order=resultat_periode_raw_order,
)

# ----------------------------------------------------------------------
# BALANSE (FØRSTE VERSJON) - eiendeler = gjeld + egenkapital, én rad per
# uke. Biologisk eiendel til akkumulert kostnad, kundefordringer/
# leverandørgjeld med betalingsutsettelse. NB: et lite gjenværende avvik
# (typisk under 0,1 % av balansesummen, størst tidlig i simuleringen) er
# ikke helt lukket ennå - vises i "Differanse"-raden for full åpenhet.
# ----------------------------------------------------------------------
st.subheader("Balanse")
st.caption(
    "Balanse for OPPDRETTER (Norway Offshore Salmon) - ikke utleier Aqualoop/SFaaS-leverandøren. '13.2 "
    "Oppankring' er utleiers eget lån, ikke oppdretterens - hele terminbeløpet er derfor ren "
    "opex her, ingen lånesaldo eller renter/avdrag-splitt. Biologisk eiendel = akkumulert "
    "påløpt ressurskostnad (0-12) for fisk som fortsatt vokser i tanken, til akkumulert kost - "
    "går i null når en kohort er ferdig levert. Kundefordringer/leverandørgjeld bruker "
    "betalingsutsettelsen fra sidepanelet. Opptjent egenkapital ruller fremover med det "
    "MATCHEDE resultatregnskapet over (kostnad bokført ved LEVERING, ikke når den påløper "
    "under vekst - nødvendig for at balansen skal gå opp). 'Differanse'-raden bør være 0 "
    "(avrundingsstøy i 7. desimal er normalt og ufarlig)."
)

balanse = build_balanse(
    cfg, ledger, cashflow, generations, fixed_costs_weekly, matchet_kostnad,
    cfg.START_ISO_YEAR, cfg.START_ISO_WEEK, kundefrist_uker=2, leverandorfrist_uker=1,
)

balanse_visning = st.selectbox(
    "Vis balanse som:", options=["Ukeoversikt", "Månedsoversikt", "Årsoversikt"], index=2,
    key="balanse_visning",
)
if balanse_visning == "Ukeoversikt":
    balanse_vist = balanse.copy()
    balanse_vist["periode"] = balanse_vist["uke"]
    # Kun tallkolonner + periode - "uke"/"dato" er tekst og kan ikke
    # tusenskille-formateres (krasjet with_thousands under).
    balanse_vist = balanse_vist[["periode"] + [c for c in balanse_vist.columns
                                                if c not in ("periode", "uke", "dato") and pd.api.types.is_numeric_dtype(balanse_vist[c])]]
    balanse_periode_labels = None
else:
    balanse_gruppe = "maned" if balanse_visning == "Månedsoversikt" else "ar"
    balanse_vist = balanse.copy()
    if balanse_gruppe == "maned":
        balanse_vist["periode"] = pd.to_datetime(balanse_vist["dato"]).dt.strftime("%Y-%m")
    else:
        balanse_vist["periode"] = pd.to_datetime(balanse_vist["dato"]).dt.isocalendar().year
    balanse_kolonner = ["kontanter", "kundefordringer", "biologisk_eiendel", "leverandorgjeld",
                         "opptjent_egenkapital", "sum_eiendeler",
                         "sum_gjeld_og_egenkapital", "differanse"]
    balanse_vist = balanse_vist.groupby("periode", sort=True)[balanse_kolonner].last().reset_index()
    balanse_periode_labels = balanse_vist["periode"].apply(month_label) if balanse_gruppe == "maned" else None

balanse_row_labels = {
    "periode": "Periode",
    "kontanter": "Kontanter (kr)",
    "kundefordringer": "Kundefordringer (kr)",
    "biologisk_eiendel": "Biologisk eiendel (kr)",
    "sum_eiendeler": "Sum eiendeler (kr)",
    "leverandorgjeld": "Leverandørgjeld (kr)",
    "opptjent_egenkapital": "Opptjent egenkapital (kr)",
    "sum_gjeld_og_egenkapital": "Sum gjeld og egenkapital (kr)",
    "differanse": "Differanse (skal være 0)",
}
balanse_formatted = with_thousands(
    balanse_vist, int_cols=[], float_cols=[c for c in balanse_vist.columns if c != "periode"], float_decimals=0,
)
balanse_periode_raw_order = balanse_vist["periode"].tolist()
if balanse_periode_labels is not None:
    balanse_formatted["periode"] = balanse_periode_labels
balanse_wide = balanse_formatted.set_index("periode").T
balanse_wide.index.name = "Felt"
balanse_wide = balanse_wide.rename(index=balanse_row_labels)

# ---- Batch-nedtrekk for KUN Biologisk eiendel, Kundefordringer og
# Leverandørgjeld - Kontanter/Opptjent egenkapital/sum-/differanse-linjene
# er hele-driften-størrelser og lar seg ikke splitte per batch. ----
balanse_period_kind = {"Ukeoversikt": "uke", "Månedsoversikt": "maned", "Årsoversikt": "ar"}[balanse_visning]
balanse_breakdown = _build_balanse_breakdown(
    batch_ukentlig, matchet_kostnad, cashflow, all_weeks_uke_dato,
    cfg.START_ISO_YEAR, cfg.START_ISO_WEEK, balanse_period_kind,
    kundefrist_uker=2, leverandorfrist_uker=1,
)
balanse_expandable = {
    "Biologisk eiendel (kr)": "biologisk_eiendel",
    "Kundefordringer (kr)": "kundefordringer",
    "Leverandørgjeld (kr)": "leverandorgjeld",
}
st.caption(
    "Klikk ▶ ved siden av Biologisk eiendel, Kundefordringer eller Leverandørgjeld for å se "
    "hvilke batcher tallet består av. Kontanter, Opptjent egenkapital og sum-/differanse-"
    "linjene er IKKE brutt ned - de er størrelser for HELE driften (kontanter er felles for "
    "alle kohorter/batcher, opptjent egenkapital er en kumulativ resultatstørrelse for hele "
    "anlegget) og lar seg ikke splitte meningsfullt per batch."
)
_render_expandable_kontantstrom(
    balanse_wide, highlight_groups=[
        {"rows": ["Sum eiendeler (kr)", "Sum gjeld og egenkapital (kr)"], "bg": "#eef7ee", "text": "#3d7a3d"},
        {"rows": ["Differanse (skal være 0)"], "bg": "#fdeaea", "text": "#a33a3a"},
    ],
    felt_bredde_px=KONSOLIDERT_FELT_BREDDE_PX, kol_bredde_px=KONSOLIDERT_KOL_BREDDE_PX,
    expandable=balanse_expandable, breakdown=balanse_breakdown, periode_raw_order=balanse_periode_raw_order,
)

# ============================================================================
# LØNNSOMHET - UTLEIER (Aqualoop/Big Dipper)
# ============================================================================
st.subheader("Big Dipper SFaaS (utleier) - Resultatregnskap, kontantstrøm og balanse")
st.caption(
    "Egne regnskaper for UTLEIER (Aqualoop, eier av anlegget) - atskilt fra oppdretters oppstillinger over. "
    "Leieinntekter = '13. Leie av Big Dipper-anlegg' (uten 13.2 Oppankring, som er et utlån til kunde: renter = "
    "finansinntekt, avdrag = nedbetaling av utlånet). Driftskostnader = 13.3-13.10 (kost-gjennomfakturering uten "
    "påslag), så EBITDA = Kapitalleien 13.1. Anlegget avskrives lineært over valgt antall år (sidepanelet); "
    "vedlikeholdsinvesteringer aktiveres og avskrives likt. Skatt med fremførbart underskudd. All fri kontantstrøm "
    "deles ut til eier hvert år (negativ = eier skyter inn) - kontantbeholdningen holdes derfor på 0, og "
    "'Kontantstrøm til eier' er nøyaktig det IRR-beregningen under bruker. Banklån, refinansiering, skattesats "
    "og vedlikehold settes i sidepanelet under 'Lønnsomhet - Utleier - forutsetninger'."
)

utleier_years = sorted({pd.to_datetime(d).year for _, d in all_weeks_uke_dato})
utleier_ar_per_uke = {u: pd.to_datetime(d).year for u, d in all_weeks_uke_dato}

# VIKTIG: bruker ISOCALENDAR-år (.dt.isocalendar().year), IKKE vanlig
# .dt.year - EKSAKT samme årsinndeling som build_konsolidert_kontantstrom()
# bruker for "13.x"-radene sine ("ar"-visning). Et par uker i året kan i
# prinsippet tilhøre et ANNET ISO-år enn sitt eget kalenderår (rundt
# årsskiftet) - bruker man vanlig .dt.year i stedet, kan en uke havne i
# feil årskolonne sammenlignet med Konsolidert kontantstrøm, og gi et
# lite, forvirrende avvik akkurat ved årsskiftet.
_fastkost_med_ar = fixed_costs_weekly.copy()
_fastkost_med_ar["ar"] = pd.to_datetime(_fastkost_med_ar["dato"]).dt.isocalendar().year
utleier_kapitalleie_per_ar = _fastkost_med_ar.groupby("ar")["kr_leie_131"].sum().to_dict()

utleier_driftskostnader_ider = ["leie_13_teknisk", "leie_133", "leie_134", "leie_136", "leie_137", "leie_138", "leie_139"]
utleier_driftskostnader_per_ar = (
    _fastkost_med_ar.groupby("ar")[[f"kr_{i}" for i in utleier_driftskostnader_ider]].sum().sum(axis=1).to_dict()
)

utleier_desinfeksjon_per_ar = {y: 0.0 for y in utleier_years}
for uke, belop in desinfeksjon_ukentlig.items():
    if uke in utleier_ar_per_uke:
        utleier_desinfeksjon_per_ar[utleier_ar_per_uke[uke]] += belop

# "13.2 Oppankring" (Finansinntekter/Avdrag fra kunde): MÅ hentes fra
# renter_avdrag_per_uke (SAMME ukentlige kilde som Konsolidert kontantstrøm
# sin "13.2 Oppankring"-rad bruker), aggregert med SAMME isocalendar-år-
# metode - IKKE regnes på nytt med en egen, måned-basert årsinndeling (det
# ga tidligere et lite, reelt avvik mot hva som faktisk belastes kunden
# samme år, siden uke-for-uke og måned-for-måned rundes ulikt ved
# årsskiftet).
_renter_avdrag_med_ar = renter_avdrag_per_uke.copy()
_renter_avdrag_med_ar["dato"] = _renter_avdrag_med_ar["uke"].map(dict(all_weeks_uke_dato))
_renter_avdrag_med_ar["ar"] = pd.to_datetime(_renter_avdrag_med_ar["dato"]).dt.isocalendar().year
utleier_oppankring_renter_per_ar = _renter_avdrag_med_ar.groupby("ar")["renter_uke"].sum().to_dict()
utleier_oppankring_avdrag_per_ar = _renter_avdrag_med_ar.groupby("ar")["avdrag_uke"].sum().to_dict()

utleier_regnskap = build_utleier_regnskap(
    utleier_years, capex, kapitalleie_ar, utleier_kapitalleie_per_ar,
    utleier_driftskostnader_per_ar, utleier_desinfeksjon_per_ar,
    oppankring_inv, utleier_oppankring_renter_per_ar, utleier_oppankring_avdrag_per_ar,
    ebitda_multipel, bankrente_pct_ar, banklan_ar_val, skattesats_pct, vedlikeholdsinvestering_pct_capex,
    vedlikeholdsinvestering_indeksering_pct_ar, prosjekt_start_dato,
    refi_intervall_ar=refi_intervall_ar, refi_multipel=refi_multipel, avskrivningstid_ar=int(avskrivningstid_ar),
)
utleier_lonnsomhet = utleier_regnskap["kombinert"]   # grunnlag for IRR (ebitda, netto kontantstrøm til eier, banklånsaldo)


def _utleier_tabell(df, labels, highlight):
    _f = with_thousands(df, int_cols=[], float_cols=[c for c in df.columns if c != "periode"], float_decimals=0)
    _w = _f.set_index("periode").T
    _w.index.name = "Felt"
    _w = _w.rename(index=labels)
    _render_table(_w, highlight_groups=highlight,
                  fixed_label_width_px=KONSOLIDERT_FELT_BREDDE_PX, fixed_data_col_width_px=KONSOLIDERT_KOL_BREDDE_PX)


st.markdown("**Resultatregnskap - utleier**")
_utleier_tabell(utleier_regnskap["resultat"], {
    "leieinntekter_kr": "Leieinntekter 13.1-13.10 (kr)", "driftskostnader_kr": "Driftskostnader 13.3-13.10 (kr)",
    "ebitda_kr": "EBITDA (= 13.1 Kapitalleie) (kr)", "avskrivninger_kr": "Avskrivninger (kr)", "ebit_kr": "EBIT (kr)",
    "finanskostnader_kr": "Finanskostnader, banklån (kr)", "finansinntekter_kr": "Finansinntekter, oppankring (kr)",
    "resultat_for_skatt_kr": "Resultat før skatt (kr)", "skatt_kr": "Skatt (kr)", "arsresultat_kr": "Årsresultat (kr)",
}, [
    {"rows": ["EBITDA (= 13.1 Kapitalleie) (kr)", "EBIT (kr)", "Resultat før skatt (kr)"], "bg": "#fbf3e6", "text": "#9a6b2a"},
    {"rows": ["Årsresultat (kr)"], "bg": "#eef7ee", "text": "#3d7a3d"},
])

st.markdown("**Kontantstrøm - utleier (indirekte metode)**")
st.caption("Negativt fortegn = utbetaling. Utbytte/innskudd = hele den frie kontantstrømmen, så 'Endring kontanter' er alltid 0.")
_utleier_tabell(utleier_regnskap["kontantstrom"], {
    "arsresultat_kr": "Årsresultat (kr)", "avskrivninger_kr": "+ Avskrivninger (kr)",
    "kfo_kr": "Kontantstrøm fra drift (kr)",
    "investering_capex_kr": "Investering i anlegg, CAPEX (kr)", "vedlikeholdsinvestering_kr": "Vedlikeholdsinvesteringer (kr)",
    "kfi_kr": "Kontantstrøm fra investering (kr)",
    "utlan_kunde_kr": "Utlån til kunde, oppankring (kr)", "avdrag_kunde_kr": "Avdrag fra kunde, oppankring (kr)",
    "banklan_opptak_kr": "Opptak banklån (kr)", "refinansiering_kr": "Refinansiering, netto proveny (kr)",
    "avdrag_bank_kr": "Avdrag banklån (kr)", "egenkapitalinnskudd_kr": "Egenkapitalinnskudd ved oppstart (kr)",
    "fri_kontantstrom_kr": "Fri kontantstrøm før utbytte (kr)", "utbytte_kr": "Utbytte til eier (-) / innskudd fra eier (+) (kr)",
    "kff_kr": "Kontantstrøm fra finansiering (kr)", "endring_kontanter_kr": "Endring kontanter (kr)",
    "kontantstrom_til_eier_kr": "Kontantstrøm til eier (IRR-grunnlag) (kr)",
}, [
    {"rows": ["Kontantstrøm fra drift (kr)", "Kontantstrøm fra investering (kr)", "Kontantstrøm fra finansiering (kr)"], "bg": "#fbf3e6", "text": "#9a6b2a"},
    {"rows": ["Fri kontantstrøm før utbytte (kr)", "Kontantstrøm til eier (IRR-grunnlag) (kr)"], "bg": "#eef7ee", "text": "#3d7a3d"},
])

st.markdown("**Balanse - utleier (31.12)**")
_utleier_tabell(utleier_regnskap["balanse"], {
    "anlegg_kostpris_kr": "Anlegg, kostpris inkl. vedlikeholdsinvesteringer (kr)", "akk_avskrivninger_kr": "Akkumulerte avskrivninger (kr)",
    "anlegg_bokfort_kr": "Anlegg, bokført verdi (kr)", "utlan_kunde_kr": "Utlån til kunde, oppankring (kr)",
    "kontanter_kr": "Kontanter (kr)", "sum_eiendeler_kr": "Sum eiendeler (kr)",
    "banklan_kr": "Banklån (kr)", "innskutt_egenkapital_kr": "Innskutt egenkapital (kr)",
    "opptjent_egenkapital_kr": "Opptjent egenkapital etter utbytte (kr)", "sum_egenkapital_kr": "Sum egenkapital (kr)",
    "sum_gjeld_egenkapital_kr": "Sum gjeld og egenkapital (kr)", "differanse_kr": "Differanse (skal være 0)",
}, [
    {"rows": ["Sum eiendeler (kr)", "Sum gjeld og egenkapital (kr)"], "bg": "#eef7ee", "text": "#3d7a3d"},
    {"rows": ["Differanse (skal være 0)"], "bg": "#fdecea", "text": "#a33"},
])

# ----------------------------------------------------------------------
# NYBYGGPARITET - guide for ny TC. Nybyggpris indeksert fra startåret,
# EBITDA-krav = yield x nybyggpris, guide-TC = EBITDA-krav + årets opex i
# leien. Sammenlignes med faktisk kapitalleie (13.1) og faktisk leie.
# ----------------------------------------------------------------------
st.subheader("Nybyggparitet - guide for ny TC på riggen")
st.caption(
    f"Nybyggpris = {fmt_int(capex)} x (1 + {byggeindeks_pct_ar*100:.1f} %) per år fra {int(cfg.START_ISO_YEAR)}. "
    f"EBITDA-krav = nybyggpris x {ebitda_yield_pct*100:.1f} %. Guide-TC = EBITDA-krav + årets driftskostnader i leien "
    "(13.3-13.10, gjennomfakturert). 'Tilsvarer kapitalleie-sats' er EBITDA-kravet delt på opprinnelig CAPEX - det "
    "satsen som ville gitt full nybyggparitet. Reforhandling velges i sidepanelet (år + andel av gapet) - de "
    "valgte nye satsene vises nederst i tabellen. Avvik = guide-TC minus faktisk leie (positivt = riggen er "
    "underpriset mot nybygg). EBITDA-yield og byggeindeks endres i sidepanelet under 13.1 Kapitalleie."
)
_np_rows = {}
_np_ar = [y for y in utleier_years if y < int(cfg.START_ISO_YEAR) + int(cfg.N_YEARS_TO_RUN)]
_kg_solgt_per_ar = summarize_cashflow_by_period(cashflow, "ar").set_index("periode")["kg_solgt"].to_dict() if len(cashflow) else {}
_leie_tot_per_ar = _fastkost_med_ar.groupby("ar")["kr_leie_anlegg"].sum().to_dict()
for y in _np_ar:
    _nyb = capex * (1 + byggeindeks_pct_ar) ** (y - int(cfg.START_ISO_YEAR))
    _krav = _nyb * ebitda_yield_pct
    _opex = utleier_driftskostnader_per_ar.get(y, 0.0) + utleier_desinfeksjon_per_ar.get(y, 0.0)
    _guide = _krav + _opex
    _fakt_131 = utleier_kapitalleie_per_ar.get(y, 0.0)
    _fakt_leie = _leie_tot_per_ar.get(y, 0.0)
    _kg = _kg_solgt_per_ar.get(y, 0.0)
    _rf_y = next((r for r in kapitalleie_reprising if r["ar"] == y), None)
    _np_rows[y] = {
        "Nybyggpris, indeksert (kr)": fmt_int(_nyb),
        "EBITDA-yield, valgt (%)": fmt_float(ebitda_yield_pct * 100, 1),
        "EBITDA-krav = yield x nybyggpris (kr)": fmt_int(_krav),
        "Tilsvarer kapitalleie-sats på opprinnelig CAPEX (%)": fmt_float(_krav / capex * 100, 1) if capex else "",
        "Årets driftskostnader i leien (kr)": fmt_int(_opex),
        "Guide-TC = EBITDA-krav + opex (kr)": fmt_int(_guide),
        "Faktisk kapitalleie 13.1 (kr)": fmt_int(_fakt_131),
        "Faktisk leie totalt 13.1-13.10 (kr)": fmt_int(_fakt_leie),
        "Avvik guide-TC - faktisk leie (kr)": fmt_int(_guide - _fakt_leie),
        f"Guide-TC per solgt kg ({solgt_enhet})": fmt_float(_guide / _kg, 2) if _kg else "",
        f"Faktisk leie per solgt kg ({solgt_enhet})": fmt_float(_fakt_leie / _kg, 2) if _kg else "",
        "Reforhandling: andel av gap hentet inn (%)": fmt_float(_rf_y["andel"] * 100, 0) if _rf_y else "",
        "Reforhandling: ny kapitalleie 13.1 (kr)": fmt_int(_rf_y["ny_131"]) if _rf_y else "",
        "Reforhandling: ny kapitalleie-sats (%)": fmt_float(_rf_y["sats_pct"] * 100, 2) if _rf_y else "",
    }
_np_wide = pd.DataFrame(_np_rows)
_np_wide.index.name = "Felt"
_render_table(_np_wide, highlight_groups=[
    {"rows": ["Guide-TC = EBITDA-krav + opex (kr)", "Tilsvarer kapitalleie-sats på opprinnelig CAPEX (%)"], "bg": "#eef7ee", "text": "#3d7a3d"},
    {"rows": ["Avvik guide-TC - faktisk leie (kr)"], "bg": "#f3eef6", "text": "#6b4f82"},
    {"rows": [f"Guide-TC per solgt kg ({solgt_enhet})", f"Faktisk leie per solgt kg ({solgt_enhet})"], "bg": "#fbf3e6", "text": "#9a6b2a"},
    {"rows": ["Reforhandling: andel av gap hentet inn (%)", "Reforhandling: ny kapitalleie 13.1 (kr)",
              "Reforhandling: ny kapitalleie-sats (%)"], "bg": "#eef1f6", "text": "#5b6b82"},
], fixed_label_width_px=KONSOLIDERT_FELT_BREDDE_PX, fixed_data_col_width_px=KONSOLIDERT_KOL_BREDDE_PX)
if kapitalleie_reprising:
    st.caption("Reforhandlinger valgt i sidepanelet (13.1 Kapitalleie): " + "; ".join(
        f"{r['ar']}: {fmt_int(r['gammel_131'])} → {fmt_int(r['ny_131'])} kr ({r['andel']*100:.0f} % av gapet til "
        f"{fmt_int(r['krav_131'])}), ny sats {r['sats_pct']*100:.2f} %" for r in kapitalleie_reprising))

if _vis_irr:
    # ============================================================================
    # IRR - UTLEIERS EGENKAPITAL, MED TERMINALVERDI VED EXIT
    # ============================================================================
    st.subheader(f"IRR - Utleiers egenkapital (eiertid {holding_years_val} år, terminalverdi år {holding_years_val})")
    st.caption(
        f"Internrente på UTLEIERS EGENKAPITAL i utleievirksomheten (ikke i selve oppdretts-"
        f"virksomheten) over {holding_years_val} år. Egenkapitalinnskudd i år 0 = CAPEX minus "
        f"utleiers eget banklån (lånefinansiert del). Årlige kontantstrømmer år 1-{holding_years_val} "
        f"er 'Netto kontantstrøm til eier' fra tabellen over. I år {holding_years_val} legges det i "
        f"tillegg til en TERMINALVERDI: {terminal_ebitda_multipel:.1f}x EBITDA året ETTER "
        f"(fremadskuende multippel - en kjøper betaler for neste års forventede inntjening, ikke "
        f"fjorårets), minus utleiers gjenværende banklånsgjeld på det tidspunktet (en kjøper "
        f"overtar normalt virksomheten gjeldfri, så selger må dekke inn resten av lånet fra "
        f"salgssummen for å finne hva egenkapitalen faktisk er verdt)."
    )

    irr_resultat = build_utleier_irr(
        utleier_lonnsomhet, capex, utleier_regnskap["banklan_belop_kr"], bankrente_pct_ar,
        int(round(banklan_ar_val * 12)), terminal_ebitda_multipel, int(holding_years_val),
    )

    if irr_resultat["irr"] is None:
        st.warning(
            "Fant ingen IRR-løsning for denne kontantstrømmen (ingen fortegnsskifte i det "
            "søkte rente-intervallet -99 % til 1000 %) - sjekk om forutsetningene gir en "
            "urealistisk kontantstrøm (f.eks. terminalverdi lavere enn gjenværende gjeld)."
        )
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("IRR", f"{irr_resultat['irr']*100:.1f} %")
        c2.metric("Egenkapitalinnskudd (år 0)", f"{fmt_int(irr_resultat['egenkapital_innskudd_kr'])} kr")
        _exit_ar = irr_resultat['terminal_ar'] - 1
        c3.metric(f"Terminalverdi (EV ved exit, utgangen av år {holding_years_val} = {_exit_ar})",
                  f"{fmt_int(irr_resultat['terminal_ev_kr'])} kr")
        c4.metric(f"Terminal egenkapitalverdi", f"{fmt_int(irr_resultat['terminal_egenkapital_kr'])} kr")
        st.caption(
            f"Terminalverdi = {terminal_ebitda_multipel:.1f} x EBITDA år {holding_years_val + 1} "
            f"({irr_resultat['terminal_ar']}, det KOMMENDE året etter exit - fremadskuende, ikke historisk): "
            f"{terminal_ebitda_multipel:.1f} x {fmt_int(irr_resultat['ebitda_terminal_kr'])} = "
            f"{fmt_int(irr_resultat['terminal_ev_kr'])} kr. År 1 = {irr_resultat['terminal_ar'] - holding_years_val}, "
            f"år {holding_years_val} = {_exit_ar}. Gjenværende banklånsgjeld ved exit (utgangen av {_exit_ar}): "
            f"{fmt_int(irr_resultat['gjeld_ved_exit_kr'])} kr."
        )

        irr_kontantstrom_df = pd.DataFrame({
            "periode": [f"År {i}" for i in range(len(irr_resultat["kontantstrom"]))],
            "Kontantstrøm til egenkapital (kr)": irr_resultat["kontantstrom"],
        })
        irr_kontantstrom_formatted = with_thousands(irr_kontantstrom_df, float_cols=["Kontantstrøm til egenkapital (kr)"], float_decimals=0)
        irr_wide = irr_kontantstrom_formatted.set_index("periode").T
        irr_wide.index.name = "Felt"
        _render_table(irr_wide, fixed_label_width_px=KONSOLIDERT_FELT_BREDDE_PX, fixed_data_col_width_px=KONSOLIDERT_KOL_BREDDE_PX)
