"""
resource_ledger.py - ressursregnskap per kohort x uke
------------------------------------------------------------------------------
Bygger et langt (tidy) ressursregnskap oppå det en scheduler (i dag:
scheduler_1tank.py) allerede har simulert - én rad per kohort x uke.

Hver rad har BÅDE kohort_id (f.eks. "K1") OG batch_id (f.eks. "K1-B1") -
se Hexacage_6tank_matfisk_notat.md for skillet: en kohort er HELE
utsettingshendelsen, en batch er én enkelt salgsuttak innenfor kohortens
slaktevindu. I DENNE 1-tank-modellen selges hele kohorten i ÉN leveranse,
så det er akkurat én batch per kohort ennå (alltid "-B1") - men feltet er
lagt inn nå slik at en fremtidig utvidelse til flere batcher per kohort
(delvis/gradert slakting over flere uker) bare krever å fylle inn flere
batch_id-verdier per kohort, ikke en ny datamodell.

Ressurslinjene er drevet av cfg.RESOURCES (se config_1tank.py), som matcher
en standard COGS-struktur for matfiskproduksjon:

  0. Kjøpt smolt        - stk, kun i innsett-uken
  1. Fôrforbruk          - kg, fra Skretting-modellen (samme som resten av
                            Hexacage-prosjektet - FCR x tilvekst x antall)
  2. MGO (Marine Gas Oil) - liter, = faktor x kg WFE produsert denne uken
  3. Oksygen             - kg O2, = faktor x kg WFE produsert denne uken
  5-12. Resten            - foreløpig ÉN flat "enhet" = 1 x kg WFE produsert
                            hver, slik at NOK/kg WFE kan fylles inn direkte
                            i cfg.RESOURCE_PRICES_NOK senere uten kodeendring

"kg WFE produsert denne uken" = BRUTTOVEKST den uken (populasjon FØR
dødelighet x vekst per fisk, summert over ukens dager) x cfg.WFE_FAKTOR -
IKKE netto biomasseendring. Dette er bevisst: bruttovekst inkluderer
tilveksten til fisk som dør i løpet av samme uke/kohort, slik at ALLE
ressurser disse fiskene har "spist av" (fôr, energi, oksygen osv.) havner i
regnskapet og til slutt belastes den batchen resten av kohorten selges i -
det forsvinner ikke bare fordi fisken døde før slakt. Samme prinsipp som
fôrforbruket allerede har brukt (feed_kg_day i simulator.py), nå brukt
konsekvent på alle ressurslinjene.

Siden 1-tank-modellen selger HELE kohorten i én leveranse, er "batchen
disse døde fiskene tilhører" alltid entydig: hele kohortens ukentlige
ressursforbruk (inkl. dødelighetens bruttovekst) summeres opp og havner i
samme kohorts "Per kohort"-rad, som per definisjon er samme batch som
selges. Når modellen senere utvides til flere salgsbatcher per kohort
(jf. Hexacage_6tank_matfisk_notat.md), er tanken at hver ukentlige rad
fortsatt kan viderefordeles til riktig batch på samme måte.

I tillegg til brutto-tallet som driver ressurslinjene, spores "kg WFE" i tre
ekstra varianter per uke, til orientering:
  - kg_wfe_brutto            = BRUTTOVEKST (se over) - samme tall som driver
                                ressurslinjene
  - kg_wfe_dodelighet        = kg_wfe_brutto MINUS kg_wfe_netto - biomassen
                                som forsvant til dødelighet denne uken
  - kg_wfe_netto             = netto biomasseendring denne uken (biomasse
                                ved uke-slutt minus forrige uke-slutt)
  - kg_wfe_netto_akkumulert  = løpende sum av kg_wfe_netto innad i kohorten
                                (holdes fast gjennom vaskeukene - batchen er
                                levert og endrer seg ikke lenger)
"""

from __future__ import annotations
import calendar
from datetime import date
import pandas as pd


def interpoler_fiskeverdi_kr_per_kg(vekt_g: float, tabell: list) -> float:
    """Interpolerer kr/kg "fiskeverdi" LINEÆRT fra en tabell av (vekt_g,
    kr_per_kg)-punkter, sortert stigende på vekt. Vekt UNDER laveste
    tabellpunkt bruker det laveste punktets sats; vekt OVER høyeste
    tabellpunkt bruker det høyeste punktets sats (INGEN ekstrapolering
    utenfor tabellens definerte område - se SMOLT_VERDITABELL_KR_PER_KG i
    config_1tank.py, der brukeren eksplisitt ba om at vekt over 1000 g skal
    bruke 1000 g sin sats).

    Treffer vekten EKSAKT et tabellpunkt, returneres akkurat DEN satsen -
    ingen interpolasjon skjer da (viktig for at f.eks. 350 g gir nøyaktig
    118,5 kr/kg fra tabellen, ikke en lineær interpolert tilnærming mellom
    naboene, siden tabellen ikke nødvendigvis er lineær mellom hvert
    punkt - se f.eks. det svake oppsvinget fra 900 g til 1000 g)."""
    if not tabell:
        return 0.0
    tabell = sorted(tabell, key=lambda p: p[0])
    if vekt_g <= tabell[0][0]:
        return tabell[0][1]
    if vekt_g >= tabell[-1][0]:
        return tabell[-1][1]
    for (v0, p0), (v1, p1) in zip(tabell, tabell[1:]):
        if v0 <= vekt_g <= v1:
            if v1 == v0:
                return p0
            andel = (vekt_g - v0) / (v1 - v0)
            return p0 + andel * (p1 - p0)
    return tabell[-1][1]  # skal ikke kunne nås, men en trygg fallback


def escalate_price(base_price: float | None, rate: float, year: int, base_year: int) -> float | None:
    """Eskalerer en pris til et gitt år, fast fra og med 1. januar det året
    (dvs. samme pris hele kalenderåret, hopper opp ved årsskiftet - ikke en
    jevn/glidende opptrapping gjennom året). `rate` er en BRØKVERDI (0,02
    for 2 %). Ingen eskalering (samme pris som basisåret) for år <= base_year.
    None gir None tilbake (ingen pris satt - skal fortsatt vise tomt).

    NB: dette er den ENKLE, ÉN-fast-sats-varianten. For ULIK sats per år
    (f.eks. 2 % de fleste år, men 3 % i 2029), bruk escalate_price_by_year()
    i stedet - det er den som faktisk brukes i modellen nå."""
    if base_price is None:
        return None
    år_differanse = max(0, year - base_year)
    return base_price * (1.0 + rate) ** år_differanse


def escalate_price_by_year(base_price: float | None, rates_by_year: dict, year: int, base_year: int) -> float | None:
    """Eskalerer en pris til et gitt år ved å multiplisere FORTLØPENDE med
    HVERT ÅRS EGEN sats, i stedet for én fast sats forrentet likt hvert år -
    slik at man kan sette f.eks. 2 % i de fleste år, men 3 % i akkurat 2029
    fordi man vet det kommer en prisøkning der.

    `rates_by_year`: dict {år: brøkverdi}, f.eks. {2027: 0.02, 2028: 0.02,
    2029: 0.03, ...}. Satsen for et gitt år gjelder økningen FRA foregående
    år TIL det året (altså: 2029-satsen brukes til å gå fra 2028-prisen til
    2029-prisen). Basisåret selv har ingen sats (pris = base_price, uendret).
    Manglende år i dict-en telles som 0 % (ingen økning det året).

    None gir None tilbake (ingen pris satt - skal fortsatt vise tomt)."""
    if base_price is None:
        return None
    if year <= base_year:
        return base_price
    pris = base_price
    for y in range(base_year + 1, year + 1):
        pris *= (1.0 + rates_by_year.get(y, 0.0))
    return pris


def build_escalation_table(cfg, years: list, extra_lines: list | None = None) -> pd.DataFrame:
    """Bygger selve indekseringstabellen til VISNING (OUTPUT) - én rad per
    pris-linje (inntekt + kostnadskomponentene 0-12 + valgfrie ekstra
    linjer, typisk 13.1-13.9/14/15 sine ÅRLIGE beløp - se `extra_lines`),
    én kolonne per år, med den faktiske eskalerte NOMINELLE verdien
    (se escalate_price_by_year()). Dette er en OUTPUT av inputtabellen med
    år-for-år-satser (cfg.ESCALATION_RATES_BY_YEAR, redigert direkte av
    brukeren i sidepanelet) - build_resource_ledger()/build_cashflow_ledger()/
    build_fixed_costs_weekly() regner selv ut de samme tallene internt fra
    nøyaktig samme satser.

    `extra_lines`: valgfri liste med dict {"id", "navn", "base_verdi"} -
    brukes for linjer som IKKE har en kr/kg-pris i RESOURCE_PRICES_NOK
    (de faste kostnadene 13.1-13.9/14/15, der "prisen" er et årlig
    kronebeløp, ikke en kr/kg-sats). "13.2 Oppankring" bør IKKE være med
    her - det er et låneavdrag med fast nedbetalingsplan, ikke noe som
    skal eskaleres."""
    rates_by_year = getattr(cfg, "ESCALATION_RATES_BY_YEAR", {})
    base_prices = getattr(cfg, "RESOURCE_PRICES_NOK", {})
    base_year = getattr(cfg, "ESCALATION_BASE_YEAR", cfg.START_ISO_YEAR)

    rows = []
    inntekt_rad = {"id": "inntekt", "navn": "Inntekt (salgspris)"}
    linjer = [inntekt_rad] + list(cfg.RESOURCES)
    for linje in linjer:
        rid = linje["id"]
        base_price = getattr(cfg, "SALES_PRICE_KR_PER_KG", 100.0) if rid == "inntekt" else base_prices.get(rid)
        rate_by_year = rates_by_year.get(rid, {})
        row = {"linje": linje["navn"]}
        for y in years:
            row[str(y)] = escalate_price_by_year(base_price, rate_by_year, y, base_year)
        rows.append(row)

    for el in (extra_lines or []):
        rid = el["id"]
        rate_by_year = rates_by_year.get(rid, {})
        row = {"linje": el["navn"]}
        for y in years:
            row[str(y)] = escalate_price_by_year(el.get("base_verdi"), rate_by_year, y, base_year)
        rows.append(row)
    return pd.DataFrame(rows)


def build_resource_ledger(cfg, cohorts, generations) -> pd.DataFrame:
    """
    cohorts: liste av scheduler_1tank.Cohort.
    generations: dict fra scheduler_1tank.build_1tank_schedule.

    Returnerer én DataFrame, én rad per kohort x uke (vekst- og vaskeuker),
    sortert på uke. For hver ressurs i cfg.RESOURCES gis to kolonner:
    "mengde_<id>" og "kr_<id>" (kr er None/tom hvis ingen pris er satt).

    Prisene eskaleres ÅRLIG (fast fra 1. januar) hvis cfg.ESCALATION_RATES
    er satt - se escalate_price(). Uten denne (default {} / ikke satt) er
    oppførselen uendret: samme flate pris gjennom hele simuleringen.
    """
    from scheduler_1tank import week_label

    resources = cfg.RESOURCES
    prices = getattr(cfg, "RESOURCE_PRICES_NOK", {})
    wfe_faktor = getattr(cfg, "WFE_FAKTOR", 1.0)
    escalation_rates_by_year = getattr(cfg, "ESCALATION_RATES_BY_YEAR", {})
    escalation_base_year = getattr(cfg, "ESCALATION_BASE_YEAR", cfg.START_ISO_YEAR)

    rows = []
    cohort_by_id = {c.id: c for c in cohorts}

    for gid, info in generations.items():
        c = cohort_by_id[gid]
        # Kohortens EGEN smoltvekt (kan variere per oppskrift i rotasjonen -
        # se scheduler_1tank.build_1tank_schedule() sin BATCH_START_WEIGHT_KG),
        # IKKE cfg.START_WEIGHT_KG direkte (som bare er GLOBAL fallback-verdien
        # brukt når ingen per-oppskrift-liste er satt).
        kohort_start_vekt_kg = info.get("start_weight_kg", cfg.START_WEIGHT_KG)
        prev_biomass_kg = info["stocked"] * kohort_start_vekt_kg  # "uke 0 sin forrige" = innsatt smoltbiomasse
        kg_wfe_netto_akkumulert = 0.0
        kg_wfe_levert_akkumulert_lopende = 0.0
        batch_ved_uke = {b["delivery_week"]: b for b in info["batches"]}
        forste_batch_id = info["batches"][0]["batch_id"]

        # ---- Vekstuker ----
        for i in range(c.n_weeks):
            wk = c.start_week + i
            lbl, d = week_label(wk, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
            is_first_week = (i == 0)

            kg_wfe_brutto = c.weekly_gross_growth_kg[i] * wfe_faktor
            kg_wfe_netto = (c.weekly_biomass_kg[i] - prev_biomass_kg) * wfe_faktor
            kg_wfe_dodelighet = kg_wfe_brutto - kg_wfe_netto  # bruttovekst MINUS dødelighetens tap = netto
            prev_biomass_kg = c.weekly_biomass_kg[i]
            kg_wfe_netto_akkumulert += kg_wfe_netto

            # LEVERT/SOLGT vekt denne uken: 0 helt til første salgsuke, deretter
            # HVER batch sin egen andel i SIN EGEN uke (avtakende andel av
            # gjenværende bestand - se scheduler_1tank._split_i_batcher()),
            # IKKE lenger nødvendigvis ett enkelt sprang i siste uke (det er
            # fortsatt tilfellet når BATCH_SALES_WINDOW_WEEKS = 1, default).
            # batch_id følger HVILKEN batch som selges DENNE uken - ukene FØR
            # salgsvinduet starter (delt vekst-/fôrhistorikk) merkes med
            # kohortens FØRSTE batch-id som en praktisk, delt "standard"-linje.
            batch_her = batch_ved_uke.get(wk)
            batch_id = batch_her["batch_id"] if batch_her else forste_batch_id
            kg_wfe_levert = batch_her["delivered_biomass_kg"] if batch_her else 0.0
            kg_wfe_levert_akkumulert_lopende += kg_wfe_levert

            row = {
                "kohort_id": gid, "batch_id": batch_id, "uke": lbl, "dato": d.isoformat(), "fase": "Vekst",
                "biomasse_kg": round(c.weekly_standing_biomass_kg[i], 1),
                "antall_fisk": round(c.weekly_standing_count[i]),
                "vekt_g": round(c.weekly_weight_kg[i] * 1000, 1),
                "kg_wfe_brutto": round(kg_wfe_brutto, 1),
                "kg_wfe_dodelighet": round(kg_wfe_dodelighet, 1),
                "kg_wfe_netto": round(kg_wfe_netto, 1),
                "kg_wfe_netto_akkumulert": round(kg_wfe_netto_akkumulert, 1),
                "kg_wfe_levert": round(kg_wfe_levert, 1),
                "kg_wfe_levert_akkumulert": round(kg_wfe_levert_akkumulert_lopende, 1),
            }
            for r in resources:
                # Ressurslinjene beregnes fortsatt av BRUTTOVEKST (jf. tidligere
                # avklaring - all ressursbruk til fisk som senere dør skal bli
                # med og belastes batchen), IKKE av netto-tallene over.
                rate_by_year = escalation_rates_by_year.get(r["id"], {})
                if r["id"] == "smolt" and hasattr(cfg, "SMOLT_PRICE_BASE_KR"):
                    # To prismodeller, valgt av cfg.SMOLT_PRIS_MODUS (satt av
                    # produkttype-presetet i sidepanelet):
                    #   "Tabell": kr/kg fra SMOLT_VERDITABELL_KR_PER_KG,
                    #       interpolert til DENNE kohortens egen vekt, x vekt
                    #       i kg = kr/stk. IKKE avhengig av vekt-i-gram-formelen
                    #       under i det hele tatt.
                    #   "Formel" (default, uendret oppførsel): fastdel + sats
                    #       x vekt i gram, som før.
                    # Begge avhenger av DENNE KOHORTENS EGEN smoltvekt - IKKE
                    # av en flat cfg.RESOURCE_PRICES_NOK["smolt"] (som uansett
                    # bare reflekterer helt ÉN, global smoltvekt og ville gitt
                    # feil pris for kohorter med en annen oppskrift/vekt i
                    # rotasjonen).
                    if getattr(cfg, "SMOLT_PRIS_MODUS", "Formel") == "Tabell":
                        vekt_g = kohort_start_vekt_kg * 1000.0
                        kr_per_kg = interpoler_fiskeverdi_kr_per_kg(vekt_g, getattr(cfg, "SMOLT_VERDITABELL_KR_PER_KG", []))
                        smolt_base_pris = kr_per_kg * kohort_start_vekt_kg
                    else:
                        smolt_base_pris = cfg.SMOLT_PRICE_BASE_KR + cfg.SMOLT_PRICE_PER_GRAM_KR * (kohort_start_vekt_kg * 1000.0)
                    eskalert_pris = escalate_price_by_year(smolt_base_pris, rate_by_year, d.year, escalation_base_year)
                else:
                    eskalert_pris = escalate_price_by_year(prices.get(r["id"]), rate_by_year, d.year, escalation_base_year)
                mengde, kr = _resource_amount(r, eskalert_pris, kg_wfe=kg_wfe_brutto,
                                               feed_kg=c.weekly_feed_kg[i] if r["kilde"] == "feed" else None,
                                               smolt_stk=info["stocked"] if (r["kilde"] == "smolt" and is_first_week) else 0)
                row[f"mengde_{r['id']}"] = mengde
                row[f"kr_{r['id']}"] = kr
            rows.append(row)

        # ---- Vaskeuker (etter levering - ingen produksjon, ingen ressursforbruk i denne modellen) ----
        siste_batch_id = info["batches"][-1]["batch_id"]
        for k in range(info["cleaning_weeks"]):
            wk = info["delivery_week"] + 1 + k
            lbl, d = week_label(wk, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
            row = {
                "kohort_id": f"({gid})", "batch_id": f"({siste_batch_id})", "uke": lbl, "dato": d.isoformat(), "fase": "Vask",
                "biomasse_kg": 0.0, "antall_fisk": 0, "vekt_g": None,
                "kg_wfe_brutto": 0.0, "kg_wfe_dodelighet": 0.0, "kg_wfe_netto": 0.0,
                "kg_wfe_netto_akkumulert": round(kg_wfe_netto_akkumulert, 1),  # holdes fast - batchen er levert
                "kg_wfe_levert": 0.0,  # ingen NY levering i vaskeuker
                "kg_wfe_levert_akkumulert": round(kg_wfe_levert_akkumulert_lopende, 1),  # holdes fast
            }
            for r in resources:
                row[f"mengde_{r['id']}"] = 0.0
                row[f"kr_{r['id']}"] = None
            rows.append(row)

    df = pd.DataFrame(rows).sort_values(["dato", "kohort_id"]).reset_index(drop=True)
    return df


def _resource_amount(r: dict, pris: float | None, kg_wfe: float, feed_kg: float | None, smolt_stk: int):
    """Regner ut (mengde, kr) for én ressurslinje på én uke-rad. `pris` er
    allerede eventuelt eskalert til riktig år av kalleren (build_resource_ledger)."""
    if r["kilde"] == "smolt":
        mengde = float(smolt_stk)
    elif r["kilde"] == "feed":
        mengde = float(feed_kg or 0.0)
    elif r["kilde"] == "wfe":
        faktor = r.get("faktor_per_kg_wfe", 1.0)
        mengde = faktor * kg_wfe
    else:
        raise ValueError(f"Ukjent kilde '{r['kilde']}' for ressurs '{r['id']}'")

    kr = round(mengde * pris, 0) if (pris is not None and mengde) else None
    return round(mengde, 2), kr


def build_cashflow_ledger(cfg, ledger: pd.DataFrame, generations: dict, sales_price_kr_per_kg: float,
                           hog_faktor: float = 1.0, seasonal_index_by_week: dict | None = None,
                           sales_price_table: list | None = None) -> pd.DataFrame:
    """Ukentlig kontantstrømtabell: inntekt (kun i leveringsuken, = SOLGT
    vekt x salgspris kr/kg), KOSTNADENE SPLITTET OPP PER RESSURSKOMPONENT
    (0-12, kr_<id>-kolonnene fra cfg.RESOURCES - ikke bare ett samlet
    kostnadstall), summert kostnad, netto kontantstrøm, og akkumulert
    kontantstrøm INNAD i hver kohort (nullstilles ved neste kohorts første
    vekstuke - "akkumulert frem til leveranse").

    `sales_price_kr_per_kg` settes i sidepanelet - en INNTEKTS-pris,
    atskilt fra RESOURCE_PRICES_NOK som kun dekker kostnadssiden.

    `sales_price_table`: valgfri liste (vekt_g, kr_per_kg)-punkter (se
    SMOLT_VERDITABELL_KR_PER_KG i config_1tank.py). Når satt, IGNORERES
    `sales_price_kr_per_kg` (den flate prisen) helt - salgsprisen slås i
    stedet opp PER BATCH, interpolert til DEN batchens egen faktiske
    leveringsvekt (batch["avg_weight_kg"]) - samme tabell og samme
    interpolasjonsfunksjon som allerede brukes for smolt-INNKJØP, nå også
    på salgssiden. Årlig eskalering (se under) gjelder fortsatt, oppå den
    tabell-oppslåtte basisprisen.

    `hog_faktor`: SOLGT vekt = levert WFE x hog_faktor. 1,0 for postsmolt
    (selges som WFE/levendevekt direkte). For slaktefisk (HOG - hodekappet
    vekt) settes denne til cfg.HOG_FAKTOR (default 0,825) - inntekt OG
    kg_wfe_levert/kg_wfe_levert_akkumulert skaleres begge med samme
    faktor, slik at "Inntekt (kr/kg)" i per-kg-tabellen fortsatt stemmer
    nøyaktig med salgsprisen (nå kr/kg HOG, ikke kr/kg WFE).

    Salgsprisen eskaleres ÅRLIG (fast fra 1. januar, samme prinsipp som
    kostnadssiden - se escalate_price()) ut fra cfg.ESCALATION_RATES sin
    "inntekt"-nøkkel, basert på LEVERINGSÅRET (ikke innsettåret).

    `seasonal_index_by_week`: valgfri dict {ISO-ukenummer (1-52/53): indeks}
    - hvis satt, MULTIPLISERES den (allerede årlig eskalerte) salgsprisen
    med indeksen for LEVERINGSUKEN sitt ISO-ukenummer, slik at prisen
    varierer gjennom året i stedet for å være flat. None (default) gir
    UENDRET oppførsel (flat pris hele året) - se cfg.USE_SEASONAL_PRICE_INDEX
    i config_1tank.py. En uke uten oppslag i dict-en (skulle normalt ikke
    skje, siden indeksen dekker uke 1-52) faller tilbake til indeks 1,0
    (ingen justering)."""
    df = ledger.sort_values("dato").reset_index(drop=True).copy()

    kr_cols = [f"kr_{r['id']}" for r in cfg.RESOURCES]
    df["kostnad_totalt_kr"] = df[kr_cols].astype(float).fillna(0.0).sum(axis=1)

    inntekt_rate_by_year = getattr(cfg, "ESCALATION_RATES_BY_YEAR", {}).get("inntekt", {})
    escalation_base_year = getattr(cfg, "ESCALATION_BASE_YEAR", cfg.START_ISO_YEAR)

    df["inntekt_kr"] = 0.0
    for gid, info in generations.items():
        for batch in info["batches"]:
            idx = df.index[(df["kohort_id"] == gid) & (df["batch_id"] == batch["batch_id"]) &
                            (df["fase"] == "Vekst") & (df["kg_wfe_levert"] > 0)]
            if len(idx) == 0:
                continue
            leverings_idx = idx[0]
            leverings_dato = pd.to_datetime(df.loc[leverings_idx, "dato"])
            leverings_ar = leverings_dato.year
            if sales_price_table:
                basispris = interpoler_fiskeverdi_kr_per_kg(batch["avg_weight_kg"] * 1000.0, sales_price_table)
            else:
                basispris = sales_price_kr_per_kg
            eskalert_salgspris = escalate_price_by_year(basispris, inntekt_rate_by_year, leverings_ar, escalation_base_year)
            if seasonal_index_by_week:
                leverings_uke = int(leverings_dato.isocalendar()[1])
                eskalert_salgspris *= seasonal_index_by_week.get(leverings_uke, 1.0)
            solgt_vekt_kg = batch["delivered_biomass_kg"] * hog_faktor
            df.loc[leverings_idx, "inntekt_kr"] = solgt_vekt_kg * eskalert_salgspris

    df["netto_kontantstrom_kr"] = df["inntekt_kr"] - df["kostnad_totalt_kr"]

    df["akkumulert_kontantstrom_kr"] = 0.0
    for gid in generations:
        mask = df["kohort_id"].isin([gid, f"({gid})"])
        df.loc[mask, "akkumulert_kontantstrom_kr"] = df.loc[mask, "netto_kontantstrom_kr"].cumsum()

    # kg_wfe_levert/-akkumulert holdes som REN WFE (ikke skalert) - dette er
    # den fysiske levendevekten. "Solgt vekt" (HOG for slaktefisk, = WFE for
    # postsmolt) er en EGEN, separat kolonne (kg_solgt/-akkumulert) - de to
    # skal IKKE blandes sammen, siden vi nå trenger BEGGE uskalerte OG
    # skalerte tall for å vise ekte "kr per kg WFE" versus "kr per kg HOG"
    # side ved side (se build_per_kg()).
    df["kg_solgt"] = df["kg_wfe_levert"] * hog_faktor
    df["kg_solgt_akkumulert"] = df["kg_wfe_levert_akkumulert"] * hog_faktor

    # Kostnadene er her splittet opp i komponentene 0-12 (kr_<id>-kolonnene,
    # samme rekkefølge som cfg.RESOURCES), IKKE bare vist som ett samlet
    # "kostnad totalt"-tall - étterspurt for å se hvilke linjer som driver
    # kontantstrømmen ukentlig, ikke bare nettoresultatet.
    output_cols = (["kohort_id", "batch_id", "uke", "dato", "fase", "inntekt_kr"] + kr_cols +
                   ["kostnad_totalt_kr", "netto_kontantstrom_kr", "akkumulert_kontantstrom_kr",
                    "kg_wfe_brutto", "kg_wfe_levert", "kg_wfe_netto_akkumulert", "kg_wfe_levert_akkumulert",
                    "kg_solgt", "kg_solgt_akkumulert", "mengde_for"])
    return df[output_cols]


def build_per_kg(df: pd.DataFrame, cost_denom_col: str = "kg_wfe_brutto",
                  revenue_denom_col: str = "kg_wfe_levert",
                  cum_denom_col: str = "kg_wfe_levert_akkumulert",
                  cost_hog_faktor: float = 1.0) -> pd.DataFrame:
    """Regner om ALLE kr-kolonner i en kontantstrøm-tabell (ukentlig,
    måned/år-aggregert, batch-filtrert, eller "totalt") til kr PER KG.

    Default (uendret oppførsel) gir kr PER KG WFE - ren levendevekt, både
    for kostnads- og inntektssiden:

      - Ressurskomponentene (0-12) OG "Kostnad totalt": delt på
        kg_wfe_brutto (SUM over perioden) - det ER grunnlaget disse
        linjene faktisk er PRISET mot.
      - "Inntekt", "Netto kontantstrøm", "Akkumulert kontantstrøm": delt på
        kg_wfe_levert (hhv. periodens sum og siste kumulative verdi) - REN
        WFE-levert vekt (IKKE HOG-justert, selv for slaktefisk).

    For SLAKTEFISK sitt "per kg HOG"-tall, kall denne med
    `revenue_denom_col="kg_solgt"`, `cum_denom_col="kg_solgt_akkumulert"`
    og `cost_hog_faktor=cfg.HOG_FAKTOR` - da blir BÅDE kostnads- og
    inntektssiden delt på HOG-vekt (bruttovekst x HOG-faktor for kostnad,
    kg_solgt = levert WFE x HOG-faktor for inntekt), og "Inntekt (kr/kg)"
    treffer salgsprisen eksakt igjen (siden inntekten selv ER beregnet på
    nøyaktig den samme HOG-vekten).

    Med kg WFE-nevneren (default) blir "Inntekt (kr/kg WFE)" DERIMOT
    lavere enn den satte salgsprisen (pris x HOG-faktor) for slaktefisk -
    det er riktig og forventet: samme kronebeløp delt på en STØRRE
    vektmengde (WFE > HOG) gir naturlig en lavere kr/kg.

    NB: "Netto kontantstrøm" er IKKE lik "Inntekt" minus "Kostnad totalt"
    ved enkel subtraksjon av kr/kg-radene, siden kostnad og inntekt her
    kan bruke ulike nevnere - netto er regnet fra de RÅ kronebeløpene og
    delt på revenue_denom_col for seg."""
    out = df.copy()
    cost_denom = (out[cost_denom_col] * cost_hog_faktor).replace(0, pd.NA) if cost_denom_col in out.columns else None
    revenue_denom = out[revenue_denom_col].replace(0, pd.NA) if revenue_denom_col in out.columns else None
    cum_denom = out[cum_denom_col].replace(0, pd.NA) if cum_denom_col in out.columns else revenue_denom

    kr_cost_cols = [c for c in out.columns if c.startswith("kr_")] + ["kostnad_totalt_kr"]
    kr_revenue_cols = ["inntekt_kr", "netto_kontantstrom_kr"]
    kr_cum_cols = ["akkumulert_kontantstrom_kr"]

    for c in kr_cost_cols:
        if c in out.columns and cost_denom is not None:
            out[c] = out[c].astype(float) / cost_denom
    for c in kr_revenue_cols:
        if c in out.columns and revenue_denom is not None:
            out[c] = out[c].astype(float) / revenue_denom
    for c in kr_cum_cols:
        if c in out.columns and cum_denom is not None:
            out[c] = out[c].astype(float) / cum_denom

    drop_cols = [c for c in ("kg_wfe_brutto", "kg_wfe_netto_akkumulert", "kg_wfe_levert", "kg_wfe_levert_akkumulert",
                              "kg_solgt", "kg_solgt_akkumulert") if c in out.columns]
    return out.drop(columns=drop_cols)


def summarize_cashflow_by_period(cashflow: pd.DataFrame, period: str) -> pd.DataFrame:
    """Aggregerer build_cashflow_ledger() sin ukentlige tabell til måneds-,
    års- eller totalnivå. `period`: "maned", "ar" eller "totalt".

    Inntekt/kostnadskomponenter/kostnad totalt/netto SUMMERES over perioden
    (de er flows). Akkumulert kontantstrøm er derimot en løpende sum, IKKE
    en flow - der tas SISTE verdi i perioden (= akkumulert kontantstrøm ved
    periodens slutt, for hvilken kohort/batch som enn var "aktiv" da).

    "totalt" gir ÉN rad/kolonne for HELE det filtrerte datasettet - typisk
    brukt for en enkelt batch, for å få hele batchens levetid oppsummert
    i ett tall per linje (i stedet for uke/måned/år for uke)."""
    if period not in ("uke", "maned", "ar", "totalt"):
        raise ValueError("period ma vaere 'uke', 'maned', 'ar' eller 'totalt'")

    df = cashflow.sort_values("dato").copy()
    if period == "uke":
        # Flere kohorter i SAMME uke (multi-tank, Big Dipper) -> summeres til
        # én rad per uke. For én tank er dette identisk med den rå tabellen.
        df["periode"] = df["uke"]
    elif period == "maned":
        df["periode"] = pd.to_datetime(df["dato"]).dt.strftime("%Y-%m")
    elif period == "ar":
        df["periode"] = pd.to_datetime(df["dato"]).dt.isocalendar().year
    else:
        df["periode"] = "Totalt"

    kr_cols = [c for c in df.columns if c.startswith("kr_")]
    sum_cols = ["inntekt_kr"] + kr_cols + ["kostnad_totalt_kr", "netto_kontantstrom_kr",
                "kg_wfe_brutto", "kg_wfe_levert", "kg_solgt", "mengde_for"]
    agg = df.groupby("periode", sort=True)[sum_cols].sum().reset_index()
    akkumulert = df.groupby("periode", sort=True)[
        ["akkumulert_kontantstrom_kr", "kg_wfe_netto_akkumulert", "kg_wfe_levert_akkumulert", "kg_solgt_akkumulert"]
    ].last().reset_index()
    return agg.merge(akkumulert, on="periode")


def build_fixed_costs_weekly(cfg, all_weeks_uke_dato: list, event_based_kr: dict | None = None) -> pd.DataFrame:
    """Genererer de faste kostnadene (13-16, se config_1tank.py FIXED_COSTS)
    for HVER kalenderuke i simuleringen - UAVHENGIG av kohort/batch (de
    påløper uansett om tanken er i vekst, vask, eller står ledig).

    "13. Leie av Big Dipper-anlegg" bygges her opp fra underlinjene 13.1-13.9
    (se cfg.HEXACAGE_LEIE_SUBLINJER / _KR_PER_UKE, satt i
    streamlit_app_1tank.py sin egen underseksjon) - hver vises som egen
    kolonne, og "kr_leie_anlegg" er ganske enkelt summen av dem.

    `event_based_kr`: valgfri dict {sublinje_id: {uke_label: kr}} for
    underlinjer som IKKE er en jevn ukentlig sats, men et engangsbeløp
    knyttet til en HENDELSE (f.eks. "13.5 Desinfeksjon" - NOK per kohort,
    kun i den uken kohorten settes inn). For disse ID-ene overstyres
    HEXACAGE_LEIE_SUBLINJER_KR_PER_UKE sin flate sats fullstendig - beløpet
    hentes fra denne oppslagstabellen (0 i uker uten hendelse).

    `all_weeks_uke_dato`: liste av (uke_label, dato_iso) - typisk hentet
    direkte fra den fulle ukentlige ressursregnskap-tabellen sine unike
    (uke, dato)-par, slik at radene her alltid dekker nøyaktig samme
    kalenderperiode som resten av modellen.

    Faste kr/uke-satser (IKKE de hendelsesbaserte i event_based_kr) eskaleres
    ÅRLIG via cfg.ESCALATION_RATES, samme prinsipp som ressurskomponentene
    0-12 (se escalate_price()) - satsen slår inn fast fra 1. januar. "13.2
    Oppankring" ligger ALLTID i event_based_kr fra kalleren og eskaleres
    ALDRI her (det er et låneavdrag med fast nedbetalingsplan)."""
    prices = getattr(cfg, "FIXED_COST_KR_PER_UKE", {})
    sublinjer = getattr(cfg, "HEXACAGE_LEIE_SUBLINJER", [])
    sub_prices = getattr(cfg, "HEXACAGE_LEIE_SUBLINJER_KR_PER_UKE", {})
    event_based_kr = event_based_kr or {}
    escalation_rates_by_year = getattr(cfg, "ESCALATION_RATES_BY_YEAR", {})
    escalation_base_year = getattr(cfg, "ESCALATION_BASE_YEAR", cfg.START_ISO_YEAR)

    rows = []
    for uke, dato in all_weeks_uke_dato:
        row = {"uke": uke, "dato": dato}
        total = 0.0
        # ISO-år (ikke kalenderår for mandagen): uke 1 i f.eks. 2031 starter
        # 30.12.2030 og skal ha 2031-satsen, slik at årssummene (som
        # aggregeres per ISO-år overalt) blir eksakt 52 x ukebeløp.
        år = int(pd.to_datetime(dato).isocalendar()[0])

        leie_sum = 0.0
        for sl in sublinjer:
            if sl["id"] in event_based_kr:
                beløp = float(event_based_kr[sl["id"]].get(uke, 0.0))
            else:
                rate_by_year = escalation_rates_by_year.get(sl["id"], {})
                beløp = escalate_price_by_year(sub_prices.get(sl["id"]), rate_by_year, år, escalation_base_year) or 0.0
            row[f"kr_{sl['id']}"] = beløp
            leie_sum += beløp
        row["kr_leie_anlegg"] = leie_sum
        total += leie_sum

        for fc in cfg.FIXED_COSTS:
            if fc["id"] == "leie_anlegg":
                continue  # dekket av underlinjene 13.1-13.9 over
            rate_by_year = escalation_rates_by_year.get(fc["id"], {})
            beløp = escalate_price_by_year(prices.get(fc["id"]), rate_by_year, år, escalation_base_year) or 0.0
            row[f"kr_{fc['id']}"] = beløp
            total += beløp

        row["kr_faste_totalt"] = total
        rows.append(row)
    return pd.DataFrame(rows)


def _legg_til_maneder(d: date, maneder: int) -> date:
    """Legger et gitt antall måneder til en dato, med korrekt håndtering av
    ulik månedslengde (samme logikk som ble brukt i streamlit_app_1tank.py
    for oppankringens nedbetalingsperiode - flyttet hit slik at
    resultatregnskapet kan gjenbruke den til å plassere renter/avdrag i
    riktig kalenderår)."""
    manedsindeks = d.month - 1 + maneder
    ar = d.year + manedsindeks // 12
    maned = manedsindeks % 12 + 1
    siste_dag_i_maned = calendar.monthrange(ar, maned)[1]
    return date(ar, maned, min(d.day, siste_dag_i_maned))


def _npv(rate: float, kontantstrom: list) -> float:
    """Nåverdi av en kontantstrøm (t=0, 1, 2, ...) ved gitt diskonteringsrente."""
    return sum(cf / (1.0 + rate) ** t for t, cf in enumerate(kontantstrom))


def _irr(kontantstrom: list, lo: float = -0.99, hi: float = 10.0, tol: float = 1e-9, max_iter: int = 200) -> float | None:
    """Enkel, robust bisection-løser for internrente (IRR) - finner renten
    der nåverdien av kontantstrømmen er 0. Antar ETT fortegnsskifte i
    kontantstrømmen (typisk: negativ egenkapitalinnskudd i t=0, positive
    kontantstrømmer resten av perioden - vanlig for en investerings-IRR).
    Returnerer None hvis det ikke finnes noe fortegnsskifte i [lo, hi]
    (f.eks. hvis kontantstrømmen aldri blir positiv nok til å dekke inn
    investeringen - ingen reell IRR finnes da uansett)."""
    f_lo, f_hi = _npv(lo, kontantstrom), _npv(hi, kontantstrom)
    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if f_lo * f_hi > 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        f_mid = _npv(mid, kontantstrom)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def build_utleier_irr(utleier_lonnsomhet: pd.DataFrame, capex: float, banklan_belop: float,
                       bankrente_pct_ar: float, banklan_maneder: int, terminal_ebitda_multipel: float,
                       holding_years: int) -> dict:
    """IRR (internrente) på UTLEIERS EGENKAPITAL i utleievirksomheten, over
    en gitt eiertid (`holding_years` år fra og med basisåret), med en
    TERMINALVERDI ved UTGANGEN av det siste holdeåret.

    Kontantstrøm til egenkapitalen:
      t=0: -(CAPEX - banklan_belop)   [egenkapitalinnskudd - resten er lånefinansiert]
      t=1..holding_years: "Netto kontantstrøm til eier" fra utleier_lonnsomhet
        (allerede etter gjeldsbetjening på UTLEIERS EGET banklån - se
        build_utleier_lonnsomhet())
      t=holding_years: PLUSS terminal EGENKAPITALverdi:
          (terminal_ebitda_multipel x EBITDA ÅRET ETTER holdeperioden)
          MINUS utestående saldo på utleiers eget banklån ved akkurat det
          tidspunktet

    Terminalverdien bruker EBITDA ÅRET ETTER holdeperioden (fremadskuende
    multippel - en vanlig eksit-konvensjon: en kjøper betaler for NESTE
    års forventede inntjening, ikke fjorårets). Terminalverdien beregnes
    som ENTERPRISE VALUE (multippel x EBITDA) MINUS gjenværende gjeld på
    utleiers eget banklån på exit-tidspunktet, siden en kjøper normalt
    overtar virksomheten gjeldfri - selger må dekke inn resterende lån
    fra salgssummen for å finne hva EGENKAPITALEN faktisk er verdt.

    Oppankringslånet (13.2) er IKKE en del av denne gjelds-nedbetalingen -
    det er kundens eget lån fra utleier og fases ut av modellen på egen
    hånd via Finansinntekter/Avdrag fra kunde, som allerede inngår i
    "Netto kontantstrøm til eier" hvert år.

    Returnerer en dict med "irr" (float eller None hvis ingen løsning),
    "kontantstrom" (liste, t=0..holding_years), "terminal_ev_kr",
    "gjeld_ved_exit_kr", "terminal_egenkapital_kr", "ebitda_terminal_kr",
    "terminal_ar" (kalenderåret EBITDA-grunnlaget er hentet fra)."""
    from formatting import annuitetsplan

    start_year = int(utleier_lonnsomhet["periode"].min())
    terminal_ar = start_year + holding_years  # året ETTER holdeperioden - EBITDA-grunnlaget

    ebitda_rad = utleier_lonnsomhet.loc[utleier_lonnsomhet["periode"] == terminal_ar, "ebitda_kr"]
    if len(ebitda_rad):
        ebitda_terminal = float(ebitda_rad.values[0])
    else:
        # Simuleringsperioden dekker ikke året etter holdeperioden - fremskriv
        # fra siste tilgjengelige år med samme vekstrate som forrige år hadde.
        siste_ar = int(utleier_lonnsomhet["periode"].max())
        ebitda_siste = float(utleier_lonnsomhet.loc[utleier_lonnsomhet["periode"] == siste_ar, "ebitda_kr"].values[0])
        forrige_rad = utleier_lonnsomhet.loc[utleier_lonnsomhet["periode"] == siste_ar - 1, "ebitda_kr"]
        vekst = (ebitda_siste / float(forrige_rad.values[0]) - 1) if len(forrige_rad) and float(forrige_rad.values[0]) else 0.0
        ebitda_terminal = ebitda_siste * (1 + vekst) ** (terminal_ar - siste_ar)

    terminal_ev = ebitda_terminal * terminal_ebitda_multipel

    # Gjeld ved exit = banklånets utgående saldo ved utgangen av siste
    # eierår, hentet fra lønnsomhetstabellen (tar hensyn til eventuelle
    # refinansieringer underveis).
    exit_ar = start_year + holding_years - 1
    if "banklan_saldo_kr" in utleier_lonnsomhet.columns:
        rad = utleier_lonnsomhet.loc[utleier_lonnsomhet["periode"] == exit_ar, "banklan_saldo_kr"]
        gjeld_ved_exit = float(rad.values[0]) if len(rad) else 0.0
    else:
        plan = annuitetsplan(banklan_belop, bankrente_pct_ar, banklan_maneder)
        maned_ved_exit = holding_years * 12
        if len(plan) == 0 or maned_ved_exit >= len(plan):
            gjeld_ved_exit = 0.0
        else:
            rad = plan[plan["maned"] == maned_ved_exit]
            gjeld_ved_exit = float(rad["utgaende_saldo"].values[0]) if len(rad) else 0.0

    terminal_egenkapital = max(0.0, terminal_ev - gjeld_ved_exit)

    egenkapital_innskudd = capex - banklan_belop
    kontantstrom = [-egenkapital_innskudd]
    for i in range(1, holding_years + 1):
        y = start_year + i - 1
        rad = utleier_lonnsomhet.loc[utleier_lonnsomhet["periode"] == y, "netto_kontantstrom_kr"]
        cf = float(rad.values[0]) if len(rad) else 0.0
        if i == holding_years:
            cf += terminal_egenkapital
        kontantstrom.append(cf)

    return {
        "irr": _irr(kontantstrom),
        "kontantstrom": kontantstrom,
        "egenkapital_innskudd_kr": egenkapital_innskudd,
        "terminal_ev_kr": terminal_ev,
        "gjeld_ved_exit_kr": gjeld_ved_exit,
        "terminal_egenkapital_kr": terminal_egenkapital,
        "ebitda_terminal_kr": ebitda_terminal,
        "terminal_ar": terminal_ar,
    }


def build_utleier_lonnsomhet(years: list, capex: float, kapitalleie_basisar: float, kapitalleie_per_ar: dict,
                              driftskostnader_per_ar: dict, desinfeksjon_per_ar: dict,
                              oppankring_renter_per_ar: dict, oppankring_avdrag_per_ar: dict,
                              ebitda_multipel: float, bankrente_pct_ar: float,
                              banklan_nedbetaling_ar: float, skattesats_pct: float,
                              vedlikeholdsinvestering_pct_capex: float,
                              vedlikeholdsinvestering_indeksering_pct_ar: float,
                              prosjekt_start_dato: date,
                              refi_intervall_ar: int = 0, refi_multipel: float = 0.0) -> pd.DataFrame:
    """Lønnsomhetsmodell for UTLEIER (Aqualoop/Big Dipper) - atskilt fra
    oppdretters egen P&L/balanse ellers i appen. Bygget år for år, samme
    kalenderår-rekke som resten av simuleringen.

    Vannrekkefølge (som spesifisert av bruker):
        Leieinntekter (13 EKSKL. 13.2 Oppankring)
      - Driftskostnader (13.3-13.9)
      = EBITDA  (= 13.1 Kapitalleie, alltid EKSAKT - se under)
      -/+ Endring arbeidskapital (satt til 0, se caption i appen for hvorfor)
      - Finanskostnader  (rente på UTLEIERS EGET banklån - finansierer CAPEX)
      + Finansinntekter  (rentedelen av 13.2 Oppankring - se under)
      = Resultat før skatt
      - Skattekostnad
      = Årsresultat
      - Avdrag til bank   (avdragsdelen av utleiers eget banklån)
      + Avdrag fra kunde  (avdragsdelen av 13.2 Oppankring)
      - Vedlikeholdsinvesteringer  (`vedlikeholdsinvestering_pct_capex` x
        CAPEX i BASISÅRET, deretter eskalert `vedlikeholdsinvestering_
        indeksering_pct_ar` PER ÅR - se escalate_price())
      = Netto kontantstrøm til eier
      Akkumulert kontantstrøm

    13.2 OPPANKRING ER BEVISST HOLDT UTENFOR "Leieinntekter": kontant-
    beløpet oppdretter betaler der er i realiteten en LÅNENEDBETALING til
    utleier (renter + avdrag på et lån utleier selv har finansiert
    ankringsinvesteringen med, og som oppdretter betaler ned over 5 år) -
    ikke leieinntekt.

    `oppankring_renter_per_ar`/`oppankring_avdrag_per_ar`: dict {år: kr} -
    MÅ hentes FERDIG AGGREGERT fra SAMME kilde som "13.2 Oppankring"-raden
    i Konsolidert kontantstrøm/Balanse (renter_avdrag_per_uke, gruppert på
    ISO-ukens kalenderår - se kalleren i streamlit_app_1tank.py), IKKE
    regnes på nytt her med build_renter_avdrag_per_ar() (måned-basert
    årsinndeling). De to metodene (uke-for-uke vs. måned-for-måned) rundes
    ULIKT ved årsskiftet, og ga tidligere et lite, men reelt avvik mellom
    "Finansinntekter + Avdrag fra kunde" her og hva som faktisk belastes
    kunden i "13.2 Oppankring" samme år.

    `driftskostnader_per_ar`: dict {år: kr} - HENTES FERDIG ESKALERT fra
    fixed_costs_weekly (13.3, 13.4, 13.6, 13.7, 13.8, 13.9 - se kalleren i
    streamlit_app_1tank.py), IKKE et flatt tall. Disse linjene eskaleres
    år for år via cfg.ESCALATION_RATES_BY_YEAR akkurat som resten av
    modellens faste kostnader. "13.1 Kapitalleie" (`kapitalleie_per_ar`)
    eskalerer nå på SAMME måte (default 2 %/år, justerbart i eskalerings-
    tabellen i sidepanelet) - `kapitalleie_basisar` (BASISÅRETS, ueskalerte
    beløp) brukes KUN til å STØRRELSESSETTE banklånet (se under), siden
    lånet inngås én gang og ikke vokser år for år selv om EBITDA gjør det.
    "13.2 Oppankring" er et låneavdrag med egen nedbetalingsplan (se over) -
    ingen av disse to er del av `driftskostnader_per_ar`.

    EBITDA = Kapitalleien (det gitte årets ESKALERTE beløp), ALLTID eksakt,
    uansett om driftskostnadene eskalerer eller ikke: siden Leieinntekter =
    Kapitalleie + Driftskostnader (SAMME driftskostnads-tall brukt på
    begge sider), kansellerer driftskostnadene seg selv ut mot inntekts-
    siden hvert år - kapitalleien ER utleiers reelle marginlinje,
    driftspostene er ren kost-gjennomfakturering uten påslag i denne
    modellen.

    Utleiers EGEN banklånsfinansiering av CAPEX er en HELT NY antakelse (se
    UTLEIER_DEFAULTS i config_1tank.py) - IKKE bekreftet mot faktiske
    lånevilkår, og finnes IKKE noe annet sted i modellen (i motsetning til
    Oppankring) - derfor ingen konsistensrisiko ved å bruke build_renter_
    avdrag_per_ar() (måned-basert årsinndeling) for AKKURAT dette lånet.
    Lånebeløpet settes som `ebitda_multipel` GANGER `kapitalleie_basisar`
    (BASISÅRETS EBITDA, ikke det enkelte års eskalerte EBITDA - lånet
    størrelsessettes én gang ved låneopptak), IKKE som andel av CAPEX - en
    vanlig covenant-form i prosjektfinansiering (Gjeld/EBITDA-multippel)
    fremfor belåningsgrad. `bankrente_pct_ar` er allerede summert (swap-
    rente + kredittpåslag) av kalleren."""
    banklan_belop = kapitalleie_basisar * ebitda_multipel
    banklan_maneder = int(round(banklan_nedbetaling_ar * 12))

    # EBITDA per år må være kjent FØR lånet, siden refinansieringen settes
    # til refi_multipel x NESTE års EBITDA. EBITDA avhenger ikke av lånet.
    ebitda_per_ar = {}
    for y in years:
        desinf = desinfeksjon_per_ar.get(y, 0.0)
        ebitda_per_ar[y] = (kapitalleie_per_ar.get(y, 0.0) + driftskostnader_per_ar.get(y, 0.0) + desinf) \
                           - (driftskostnader_per_ar.get(y, 0.0) + desinf)
    bank = banklan_med_refinansiering(
        banklan_belop, bankrente_pct_ar, banklan_maneder, prosjekt_start_dato, years,
        refi_intervall_ar, refi_multipel, ebitda_per_ar,
    )
    bank_renter_per_ar = dict(zip(bank["ar"], bank["renter"]))
    bank_avdrag_per_ar = dict(zip(bank["ar"], bank["avdrag"]))
    bank_refi_per_ar = dict(zip(bank["ar"], bank["refi_proveny"]))
    bank_saldo_per_ar = dict(zip(bank["ar"], bank["utgaende_saldo"]))

    vedlikehold_basis_ar = capex * vedlikeholdsinvestering_pct_capex
    vedlikehold_basisar = min(years) if years else prosjekt_start_dato.year

    rows = []
    akkumulert = 0.0
    for y in years:
        desinf = desinfeksjon_per_ar.get(y, 0.0)
        kapitalleie_eskalert = kapitalleie_per_ar.get(y, 0.0)
        driftskostnader_eskalert = driftskostnader_per_ar.get(y, 0.0)
        leieinntekter = kapitalleie_eskalert + driftskostnader_eskalert + desinf
        driftskostnader = driftskostnader_eskalert + desinf
        ebitda = leieinntekter - driftskostnader
        arbeidskapital_endring = 0.0
        finanskostnader = bank_renter_per_ar.get(y, 0.0)
        finansinntekter = oppankring_renter_per_ar.get(y, 0.0)
        resultat_for_skatt = ebitda - arbeidskapital_endring - finanskostnader + finansinntekter
        skatt = max(0.0, resultat_for_skatt) * skattesats_pct
        arsresultat = resultat_for_skatt - skatt
        avdrag_bank = bank_avdrag_per_ar.get(y, 0.0)
        avdrag_kunde = oppankring_avdrag_per_ar.get(y, 0.0)
        vedlikehold_ar = escalate_price(vedlikehold_basis_ar, vedlikeholdsinvestering_indeksering_pct_ar, y, vedlikehold_basisar)
        refi_proveny = bank_refi_per_ar.get(y, 0.0)   # nytt lån minus innfridd saldo -> kontant til eier
        netto_kontantstrom = arsresultat - avdrag_bank + avdrag_kunde - vedlikehold_ar + refi_proveny
        akkumulert += netto_kontantstrom
        rows.append({
            "periode": y,
            "leieinntekter_kr": leieinntekter,
            "driftskostnader_kr": driftskostnader,
            "ebitda_kr": ebitda,
            "arbeidskapital_kr": arbeidskapital_endring,
            "finanskostnader_kr": finanskostnader,
            "finansinntekter_kr": finansinntekter,
            "resultat_for_skatt_kr": resultat_for_skatt,
            "skatt_kr": skatt,
            "arsresultat_kr": arsresultat,
            "avdrag_bank_kr": avdrag_bank,
            "avdrag_kunde_kr": avdrag_kunde,
            "vedlikeholdsinvestering_kr": vedlikehold_ar,
            "refinansiering_kr": refi_proveny,
            "netto_kontantstrom_kr": netto_kontantstrom,
            "akkumulert_kontantstrom_kr": akkumulert,
            "banklan_saldo_kr": bank_saldo_per_ar.get(y, 0.0),
        })
    return pd.DataFrame(rows)


def build_utleier_regnskap(years: list, capex: float, kapitalleie_basisar: float, kapitalleie_per_ar: dict,
                            driftskostnader_per_ar: dict, desinfeksjon_per_ar: dict,
                            oppankring_inv: float, oppankring_renter_per_ar: dict, oppankring_avdrag_per_ar: dict,
                            ebitda_multipel: float, bankrente_pct_ar: float, banklan_nedbetaling_ar: float,
                            skattesats_pct: float, vedlikeholdsinvestering_pct_capex: float,
                            vedlikeholdsinvestering_indeksering_pct_ar: float, prosjekt_start_dato: date,
                            refi_intervall_ar: int = 0, refi_multipel: float = 0.0,
                            avskrivningstid_ar: int = 25) -> dict:
    """Fullt regnskap for UTLEIER (Big Dipper SFaaS / Aqualoop) per år:
    RESULTATREGNSKAP, KONTANTSTRØM (indirekte) og BALANSE - konsistente med
    hverandre (balansen går i null). Erstatter den gamle vannrekkefølgen
    (build_utleier_lonnsomhet) som grunnlag for IRR.

    Forutsetninger:
      - Anlegget (CAPEX) aktiveres 1. januar startåret og avskrives LINEÆRT
        over `avskrivningstid_ar`. Vedlikeholdsinvesteringer aktiveres året
        de gjøres og avskrives lineært over samme antall år fra året etter.
      - Finansiering ved oppstart: banklån = ebitda_multipel x kapitalleie
        basisår, resten egenkapitalinnskudd. Refinansiering som i
        banklan_med_refinansiering() (proveny = nytt lån - innfridd saldo).
      - Skatt = skattesats x (resultat før skatt), med FREMFØRBART UNDERSKUDD.
        (Avskrivninger er skattemessig fradragsberettiget - i motsetning
        til den gamle vannrekkefølgen, som skattla EBITDA - renter.)
      - Oppankringslånet til kunde (13.2) er et UTLÅN i balansen: renter =
        finansinntekt, avdrag = nedbetaling av utlånet.
      - Utbytte til eier = all fri kontantstrøm hvert år (positiv), og
        eier skyter inn egenkapital ved negativ fri kontantstrøm - dvs.
        kontantbeholdningen holdes på 0 og "Kontantstrøm til eier" er
        NØYAKTIG det IRR-beregningen bruker (samme tall, samme fortegn).
    Returnerer {"resultat": df, "kontantstrom": df, "balanse": df,
    "kombinert": df} - "kombinert" har kolonnene build_utleier_irr trenger."""
    banklan_belop = kapitalleie_basisar * ebitda_multipel
    banklan_maneder = int(round(banklan_nedbetaling_ar * 12))
    ebitda_per_ar = {}
    for y in years:
        d_ = desinfeksjon_per_ar.get(y, 0.0)
        ebitda_per_ar[y] = kapitalleie_per_ar.get(y, 0.0)   # leie - driftskost = kapitalleien (kost-gjennomfakturering)
    bank = banklan_med_refinansiering(banklan_belop, bankrente_pct_ar, banklan_maneder, prosjekt_start_dato, years,
                                      refi_intervall_ar, refi_multipel, ebitda_per_ar)
    bank_renter = dict(zip(bank["ar"], bank["renter"]))
    bank_avdrag = dict(zip(bank["ar"], bank["avdrag"]))
    bank_refi = dict(zip(bank["ar"], bank["refi_proveny"]))
    bank_saldo = dict(zip(bank["ar"], bank["utgaende_saldo"]))

    vedl_basis = capex * vedlikeholdsinvestering_pct_capex
    y0 = min(years) if years else prosjekt_start_dato.year
    n_avskr = max(1, int(avskrivningstid_ar))

    res_rows, cf_rows, bal_rows, komb_rows = [], [], [], []
    # Anleggsmidler: liste av (kostpris, år aktivert) - hver avskrives lineært
    eiendeler = [(capex, y0)]
    akk_avskr = 0.0
    utlan_kunde = float(oppankring_inv or 0.0)
    egenkapital_innskudd_akk = capex - banklan_belop   # år 0
    opptjent_ek = 0.0
    utbytte_akk = 0.0
    fremforbart = 0.0
    saldo_ib = banklan_belop
    for y in years:
        desinf = desinfeksjon_per_ar.get(y, 0.0)
        drift = driftskostnader_per_ar.get(y, 0.0) + desinf
        leie = kapitalleie_per_ar.get(y, 0.0) + drift
        ebitda = leie - drift
        # Avskrivninger: hvert aktivum lineært over n_avskr år fra og med
        # året etter aktivering (CAPEX fra og med startåret selv).
        avskr = 0.0
        for kost, ar_akt in eiendeler:
            forste = ar_akt if ar_akt == y0 else ar_akt + 1
            if forste <= y < forste + n_avskr:
                avskr += kost / n_avskr
        ebit = ebitda - avskr
        renter = bank_renter.get(y, 0.0)
        finansinnt = oppankring_renter_per_ar.get(y, 0.0)
        rfs = ebit - renter + finansinnt
        grunnlag = rfs - fremforbart
        if grunnlag > 0:
            skatt = grunnlag * skattesats_pct
            fremforbart = 0.0
        else:
            skatt = 0.0
            fremforbart = -grunnlag
        arsres = rfs - skatt
        # Kontantstrøm (indirekte)
        kfo = arsres + avskr
        vedl = escalate_price(vedl_basis, vedlikeholdsinvestering_indeksering_pct_ar, y, y0)
        invest_capex = capex if y == y0 else 0.0
        kfi = -invest_capex - vedl
        avdrag = bank_avdrag.get(y, 0.0)
        refi = bank_refi.get(y, 0.0)
        avdrag_kunde = oppankring_avdrag_per_ar.get(y, 0.0)
        utlan_ny = utlan_kunde if y == y0 else 0.0   # utlån til kunde gis ved oppstart
        opptak = banklan_belop if y == y0 else 0.0
        ek_innskudd = (capex - banklan_belop) if y == y0 else 0.0
        fri_kontantstrom = kfo + kfi + opptak + refi - avdrag - utlan_ny + avdrag_kunde + ek_innskudd
        # Alt fritt deles ut (positivt) / dekkes av eier (negativt) -> kontanter = 0
        utbytte = fri_kontantstrom
        kff = opptak + refi - avdrag + ek_innskudd - utbytte
        endring_kontanter = kfo + kfi + kff - utlan_ny + avdrag_kunde
        # Balanse
        akk_avskr += avskr
        eiendeler.append((vedl, y))
        anlegg_kost = sum(k for k, _ in eiendeler)
        anlegg_bokfort = anlegg_kost - akk_avskr
        utlan_kunde = max(0.0, utlan_kunde - avdrag_kunde)
        opptjent_ek += arsres
        utbytte_akk += max(0.0, utbytte)
        ek_innskudd_akk = (capex - banklan_belop) + sum(-min(0.0, r["kontantstrom_til_eier_kr"]) for r in cf_rows)
        if utbytte < 0:
            ek_innskudd_akk += -utbytte
        gjeld = bank_saldo.get(y, 0.0)
        egenkapital = ek_innskudd_akk + opptjent_ek - utbytte_akk
        sum_eiend = anlegg_bokfort + utlan_kunde + 0.0
        sum_gjeld_ek = gjeld + egenkapital
        # kontantstrøm til eier (IRR-grunnlag) = utbytte (negativt = innskudd)
        kontantstrom_til_eier = utbytte
        res_rows.append({"periode": y, "leieinntekter_kr": leie, "driftskostnader_kr": drift, "ebitda_kr": ebitda,
                         "avskrivninger_kr": avskr, "ebit_kr": ebit, "finanskostnader_kr": renter,
                         "finansinntekter_kr": finansinnt, "resultat_for_skatt_kr": rfs, "skatt_kr": skatt,
                         "arsresultat_kr": arsres})
        cf_rows.append({"periode": y,
                        # DIREKTE metode: inn-/utbetalinger fra drift (ingen arbeidskapital i
                        # utleiermodellen, så summen = årsresultat + avskrivninger)
                        "innbet_leie_kr": leie, "utbet_drift_kr": -drift, "utbet_renter_kr": -renter,
                        "innbet_renter_kunde_kr": finansinnt, "utbet_skatt_kr": -skatt,
                        "arsresultat_kr": arsres, "avskrivninger_kr": avskr, "kfo_kr": kfo,
                        "investering_capex_kr": -invest_capex, "vedlikeholdsinvestering_kr": -vedl, "kfi_kr": kfi,
                        "utlan_kunde_kr": -utlan_ny, "avdrag_kunde_kr": avdrag_kunde,
                        "banklan_opptak_kr": opptak, "refinansiering_kr": refi, "avdrag_bank_kr": -avdrag,
                        "egenkapitalinnskudd_kr": ek_innskudd, "fri_kontantstrom_kr": fri_kontantstrom,
                        "utbytte_kr": -utbytte, "kff_kr": kff, "endring_kontanter_kr": endring_kontanter,
                        "kontantstrom_til_eier_kr": kontantstrom_til_eier})
        bal_rows.append({"periode": y, "anlegg_kostpris_kr": anlegg_kost, "akk_avskrivninger_kr": -akk_avskr,
                         "anlegg_bokfort_kr": anlegg_bokfort, "utlan_kunde_kr": utlan_kunde, "kontanter_kr": 0.0,
                         "sum_eiendeler_kr": sum_eiend, "banklan_kr": gjeld, "innskutt_egenkapital_kr": ek_innskudd_akk,
                         "opptjent_egenkapital_kr": opptjent_ek - utbytte_akk, "sum_egenkapital_kr": egenkapital,
                         "sum_gjeld_egenkapital_kr": sum_gjeld_ek, "differanse_kr": sum_eiend - sum_gjeld_ek})
        komb_rows.append({"periode": y, "ebitda_kr": ebitda, "netto_kontantstrom_kr": kontantstrom_til_eier,
                          "banklan_saldo_kr": gjeld})
    return {"resultat": pd.DataFrame(res_rows), "kontantstrom": pd.DataFrame(cf_rows),
            "balanse": pd.DataFrame(bal_rows), "kombinert": pd.DataFrame(komb_rows),
            "banklan_belop_kr": banklan_belop}


def build_eier_uke(cfg, alle_uker_uke_dato: list, capex: float, banklan_belop: float, bankrente_pct_ar: float,
                   banklan_maneder: int, prosjekt_start_dato: date, vedlikehold_basis_ar: float,
                   vedlikehold_indeks_pct_ar: float, avskrivningstid_ar: int) -> pd.DataFrame:
    """KONSOLIDERT visning: anlegget eies av OPPDRETTER selv (ingen leie). Én
    rad per uke med eierens egne poster, klare til å legges inn i
    Konsolidert kontantstrøm, Resultatregnskap og Balanse:
      capex_kr        - investering i anlegget (uke 0)
      lan_opptak_kr   - banklån tatt opp (uke 0)
      ek_innskudd_kr  - egenkapitalinnskudd (uke 0) = CAPEX - lån
      renter_kr / avdrag_kr - banklånets annuitet (månedsplan fordelt på uker)
      vedlikehold_kr  - vedlikeholdsinvesteringer (aktiveres), NOK/år fordelt /52
      avskrivning_kr  - lineær avskrivning av CAPEX + aktiverte vedlikeholdsinv.
      lan_saldo_kr    - utgående lånesaldo, anlegg_bokfort_kr - bokført verdi
    Ingen refinansiering her (konsolidert = ren driftsmodell)."""
    uker = [u for u, _ in alle_uker_uke_dato]
    datoer = {u: d for u, d in alle_uker_uke_dato}
    ra = build_renter_avdrag_per_uke(banklan_belop, bankrente_pct_ar, banklan_maneder, prosjekt_start_dato, uker, datoer)
    renter = dict(zip(ra["uke"], ra["renter_uke"]))
    avdrag = dict(zip(ra["uke"], ra["avdrag_uke"]))
    y0 = int(pd.to_datetime(datoer[uker[0]]).isocalendar()[0]) if uker else prosjekt_start_dato.year
    n = max(1, int(avskrivningstid_ar))
    rows, saldo, akk_avskr, aktivert = [], float(banklan_belop), 0.0, [(float(capex), y0)]
    vedl_akt_ar = {}
    for i, u in enumerate(uker):
        y = int(pd.to_datetime(datoer[u]).isocalendar()[0])
        vedl_ar = escalate_price(vedlikehold_basis_ar, vedlikehold_indeks_pct_ar, y, y0)
        vedl = vedl_ar / 52.0
        vedl_akt_ar[y] = vedl_akt_ar.get(y, 0.0) + vedl
        # avskrivning per uke: CAPEX fra uke 0; vedlikehold fra året etter aktivering
        avskr = capex / n / 52.0 if y < y0 + n else 0.0
        # vedlikehold aktivert i år a avskrives lineært fra år a+1 i n år
        avskr += sum(b / n / 52.0 for a, b in _vedl_sum_per_ar(vedl_akt_ar).items() if a + 1 <= y < a + 1 + n)
        r_, a_ = renter.get(u, 0.0), avdrag.get(u, 0.0)
        saldo = max(0.0, saldo - a_)
        akk_avskr += avskr
        rows.append({
            "uke": u, "dato": datoer[u],
            "capex_kr": capex if i == 0 else 0.0,
            "lan_opptak_kr": banklan_belop if i == 0 else 0.0,
            "ek_innskudd_kr": (capex - banklan_belop) if i == 0 else 0.0,
            "renter_kr": r_, "avdrag_kr": a_, "vedlikehold_kr": vedl, "avskrivning_kr": avskr,
            "lan_saldo_kr": saldo,
            "anlegg_kostpris_kr": capex + sum(vedl_akt_ar.values()),
            "akk_avskrivning_kr": akk_avskr,
            "anlegg_bokfort_kr": capex + sum(vedl_akt_ar.values()) - akk_avskr,
        })
    return pd.DataFrame(rows)


def _vedl_sum_per_ar(vedl_akt_ar: dict) -> dict:
    """Vedlikeholdsinvesteringer aktivert per år (kun HELE tidligere år
    teller som ferdig aktivert - inneværende år bygges opp uke for uke og
    begynner å avskrives året etter)."""
    return dict(vedl_akt_ar)


def banklan_med_refinansiering(belop0: float, rente_pct_ar: float, maneder: int, start_dato: date,
                               years: list, refi_intervall_ar: int, refi_multipel: float,
                               ebitda_per_ar: dict) -> pd.DataFrame:
    """Utleiers banklån måned for måned, med REFINANSIERING hvert
    `refi_intervall_ar` år (0 = av): ved refinansieringstidspunktet innfris
    gjenværende saldo og et NYTT lån tas opp på `refi_multipel` x NESTE
    kalenderårs EBITDA, med ny annuitet over `maneder` måneder. Differansen
    (nytt lån - innfridd saldo) er et kontant PROVENY til eier (positivt
    når EBITDA/multippelen har vokst - det er dette som løfter IRR).
    Returnerer per år: renter, avdrag, refi_proveny, utgaende_saldo."""
    r = rente_pct_ar / 12.0
    def _termin(P, n):
        if n <= 0 or P <= 0:
            return 0.0
        return P / n if r == 0 else P * r / (1 - (1 + r) ** (-n))

    saldo = float(belop0)
    termin = _termin(saldo, maneder)
    gjenv = maneder
    per_ar = {y: {"renter": 0.0, "avdrag": 0.0, "refi_proveny": 0.0, "utgaende_saldo": 0.0} for y in years}
    n_mnd_tot = len(years) * 12
    for m in range(n_mnd_tot):
        d = _legg_til_maneder_dato(start_dato, m)
        y = d.year
        if y not in per_ar:
            break
        if refi_intervall_ar > 0 and m > 0 and m % (refi_intervall_ar * 12) == 0:
            neste_ebitda = ebitda_per_ar.get(y + 1, ebitda_per_ar.get(y, 0.0))
            nytt = max(0.0, refi_multipel * neste_ebitda)
            per_ar[y]["refi_proveny"] += nytt - saldo
            saldo = nytt
            termin = _termin(saldo, maneder)
            gjenv = maneder
        if saldo > 0 and gjenv > 0:
            renter = saldo * r
            avdrag = min(saldo, termin - renter)
            saldo -= avdrag
            gjenv -= 1
            per_ar[y]["renter"] += renter
            per_ar[y]["avdrag"] += avdrag
        per_ar[y]["utgaende_saldo"] = max(0.0, saldo)
    return pd.DataFrame([{"ar": y, **v} for y, v in per_ar.items()])


def _legg_til_maneder_dato(d: date, n: int) -> date:
    y, m = d.year, d.month + n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1)


def build_renter_avdrag_per_ar(oppankring_inv: float, oppankring_rente: float, oppankring_mnd: int,
                                prosjekt_start_dato: date) -> pd.DataFrame:
    """Fordeler et annuitetslåns renter/avdrag (fra formatting.annuitetsplan())
    på KALENDERÅR, ut fra hvilket år hver av de N nedbetalingsmånedene faller
    i (måned 1 = prosjektstartmåneden). Brukes til å skille renter (skal inn
    i resultatregnskapet som en finanskostnad) fra avdrag (skal KUN redusere
    lånesaldoen i balansen, ikke belaste resultatet)."""
    from formatting import annuitetsplan
    plan = annuitetsplan(oppankring_inv, oppankring_rente, oppankring_mnd)
    if len(plan) == 0:
        return pd.DataFrame(columns=["ar", "renter", "avdrag"])
    rows = []
    for _, rad in plan.iterrows():
        d = _legg_til_maneder(prosjekt_start_dato, int(rad["maned"]) - 1)
        rows.append({"ar": d.year, "renter": rad["renter"], "avdrag": rad["avdrag"]})
    return pd.DataFrame(rows).groupby("ar", sort=True)[["renter", "avdrag"]].sum().reset_index()


def build_renter_avdrag_per_uke(oppankring_inv: float, oppankring_rente: float, oppankring_mnd: int,
                                 prosjekt_start_dato: date, alle_uker: list, uke_til_dato: dict) -> pd.DataFrame:
    """Samme annuitetsplan som build_renter_avdrag_per_ar(), men fordelt
    JEVNT UTOVER DE FAKTISKE KALENDERUKENE i hver nedbetalingsmåned - IKKE
    aggregert til år først. Uten dette hopper lånesaldoen i balansen kun
    ÅRSVIS (ved hvert årsskifte), mens kontantene betales ut JEVNT HVER UKE
    - de to stemmer da bare overens ved årsskiftene, ikke uke for uke, som
    var akkurat den gjenværende differansen i balansen.

    Returnerer én rad per uke i `alle_uker`, med "renter_uke", "avdrag_uke"
    og "utgaende_saldo" (lånesaldo etter den ukens avdrag - 0 for uker
    utenfor selve nedbetalingsperioden)."""
    from formatting import annuitetsplan
    plan = annuitetsplan(oppankring_inv, oppankring_rente, oppankring_mnd)
    renter_uke_map, avdrag_uke_map = {}, {}

    if len(plan) > 0:
        for _, rad in plan.iterrows():
            maned_start = _legg_til_maneder(prosjekt_start_dato, int(rad["maned"]) - 1)
            maned_slutt = _legg_til_maneder(prosjekt_start_dato, int(rad["maned"]))  # eksklusiv (start på neste måned)
            uker_i_maneden = [u for u in alle_uker if maned_start <= pd.to_datetime(uke_til_dato[u]).date() < maned_slutt]
            n = len(uker_i_maneden)
            if n == 0:
                continue  # svært usannsynlig (en måned uten en eneste ukestart), men ikke la det knekke fordelingen
            for u in uker_i_maneden:
                renter_uke_map[u] = renter_uke_map.get(u, 0.0) + rad["renter"] / n
                avdrag_uke_map[u] = avdrag_uke_map.get(u, 0.0) + rad["avdrag"] / n

    hovedstol = float(plan["avdrag"].sum()) if len(plan) else 0.0
    rows = []
    saldo = hovedstol
    for u in alle_uker:
        avdrag_denne_uken = avdrag_uke_map.get(u, 0.0)
        saldo = max(0.0, saldo - avdrag_denne_uken)
        rows.append({"uke": u, "renter_uke": renter_uke_map.get(u, 0.0),
                     "avdrag_uke": avdrag_denne_uken, "utgaende_saldo": saldo})
    return pd.DataFrame(rows)


def build_resultatregnskap(cfg, cashflow: pd.DataFrame, fixed_costs_weekly: pd.DataFrame,
                            matchet_kostnad: pd.DataFrame, start_year: int, start_week: int,
                            period: str) -> pd.DataFrame:
    """Konsolidert RESULTATREGNSKAP (IKKE kontantstrøm) - periodisert etter
    når inntekt/kostnad faktisk PÅLØPER (levering), ikke når kontanter
    beveger seg. Kostnadene (0-12) er MATCHET mot levering (se
    build_matchet_kostnad_per_uke()) - IKKE bokført når de løper under
    vekst - slik at "Bruttofortjeneste" alltid stemmer med samme periodes
    inntekt (matching-prinsippet), og slik at dette resultatregnskapet
    bruker EKSAKT samme grunnlag som build_balanse() sin opptjente
    egenkapital (uten det ville de to oppstillingene vist ulike tall).

    "13.2 Oppankring" er Tridents (SFaaS-leverandørens) eget lån, IKKE
    oppdretterens (Bremnes Seashore) - hele terminbeløpet er derfor REN
    OPEX for oppdretter, på linje med de andre 13.x-linjene. INGEN
    renter/avdrag-splitt, INGEN finanskostnad, INGEN lånesaldo noe sted i
    oppdretterens egne oppstillinger.

    Struktur (norsk oppstillingsplan, forenklet):
      Driftsinntekter
      - Varekostnad (0-12, ekskl. lønn - "vareforbruk"), MATCHET mot levering
      = Bruttofortjeneste (dekningsbidrag)
      - Lønnskostnader (6, 11, 13.6, 13.7), MATCHET mot levering
      - Andre driftskostnader (13.1-13.5/13.8/13.9/14/15, INKL. 13.2), PÅLØPT
      = Årsresultat før skatt

    Ingen skattekostnad er lagt inn ennå - "Årsresultat før skatt" er
    dermed også bunnlinjen inntil videre."""
    from scheduler_1tank import week_label

    sublinjer = getattr(cfg, "HEXACAGE_LEIE_SUBLINJER", [])
    lonns_ider = {"annet_direkte_lonn", "indirekte_lonn"}
    lonns_sublinje_ider = {"leie_136", "leie_137"}
    resource_ider = [r["id"] for r in cfg.RESOURCES]

    # ---- MATCHET varekostnad/lønn per uke (bygges én gang, i uke-oppløsning,
    #      slik at "uke"/"maned"/"ar" alle kan aggregeres fra samme kilde) ----
    matchet_lonn_uke, matchet_andre_uke = {}, {}
    matchet_linje_uke = {rid: {} for rid in resource_ider}   # per ressurslinje 0-12 (matchet)
    for _, rad in matchet_kostnad.iterrows():
        lbl, _ = week_label(rad["uke_idx"], start_year, start_week)
        lonn_del = sum(rad[f"kr_{rid}_matchet"] for rid in resource_ider if rid in lonns_ider)
        andre_del = sum(rad[f"kr_{rid}_matchet"] for rid in resource_ider if rid not in lonns_ider)
        matchet_lonn_uke[lbl] = matchet_lonn_uke.get(lbl, 0.0) + lonn_del
        matchet_andre_uke[lbl] = matchet_andre_uke.get(lbl, 0.0) + andre_del
        for rid in resource_ider:
            matchet_linje_uke[rid][lbl] = matchet_linje_uke[rid].get(lbl, 0.0) + rad[f"kr_{rid}_matchet"]
    matchet_uke_df = pd.DataFrame({
        "uke": list(set(matchet_lonn_uke) | set(matchet_andre_uke)),
    })
    matchet_uke_df["varekostnad_kr"] = matchet_uke_df["uke"].map(matchet_andre_uke).fillna(0.0)
    matchet_uke_df["lonnskostnader_kr"] = matchet_uke_df["uke"].map(matchet_lonn_uke).fillna(0.0)
    linje_kolonner = [f"kr_{rid}_matchet" for rid in resource_ider]
    for rid in resource_ider:
        matchet_uke_df[f"kr_{rid}_matchet"] = matchet_uke_df["uke"].map(matchet_linje_uke[rid]).fillna(0.0)
    # Solgt kg (HOG/WFE etter cfg) per uke - grunnlag for "per solgt kg"-radene
    kg_solgt_uke = cashflow.groupby("uke", sort=False)["kg_solgt"].sum()

    if period == "uke":
        # Summert per uke - med flere kohorter/tanker i samme uke (Big
        # Dipper) ville rå cashflow-rader gitt duplikate uker og
        # multiplisert matchet kostnad/faste kostnader i merge under.
        variabel = cashflow.groupby("uke", sort=False)[["inntekt_kr", "kg_solgt"]].sum().reset_index()
        variabel["periode"] = variabel["uke"]
        fast = fixed_costs_weekly.copy()
        fast["periode"] = fast["uke"]
        matchet_periode = matchet_uke_df.copy()
        matchet_periode["periode"] = matchet_periode["uke"]
    elif period in ("maned", "ar"):
        inntekt_uke_serie = cashflow.groupby("uke", sort=False)[["inntekt_kr", "kg_solgt"]].sum().reset_index()
        dato_per_uke = cashflow.drop_duplicates("uke")[["uke", "dato"]]
        inntekt_uke_serie = inntekt_uke_serie.merge(dato_per_uke, on="uke")
        fast = fixed_costs_weekly.copy()
        matchet_periode = matchet_uke_df.merge(dato_per_uke, on="uke", how="left")
        if period == "maned":
            inntekt_uke_serie["periode"] = pd.to_datetime(inntekt_uke_serie["dato"]).dt.strftime("%Y-%m")
            fast["periode"] = pd.to_datetime(fast["dato"]).dt.strftime("%Y-%m")
            matchet_periode["periode"] = pd.to_datetime(matchet_periode["dato"]).dt.strftime("%Y-%m")
        else:
            inntekt_uke_serie["periode"] = pd.to_datetime(inntekt_uke_serie["dato"]).dt.isocalendar().year
            fast["periode"] = pd.to_datetime(fast["dato"]).dt.isocalendar().year
            matchet_periode["periode"] = pd.to_datetime(matchet_periode["dato"]).dt.isocalendar().year
        variabel = inntekt_uke_serie.groupby("periode", sort=True)[["inntekt_kr", "kg_solgt"]].sum().reset_index()
    else:
        raise ValueError("period ma vaere 'uke', 'maned' eller 'ar'")

    matchet_sum = (matchet_periode.groupby("periode", sort=True)[["varekostnad_kr", "lonnskostnader_kr"] + linje_kolonner]
                   .sum().reset_index())
    fast_kolonner = ([f"kr_{sl['id']}" for sl in sublinjer] + ["kr_leie_anlegg"] +
                      [f"kr_{fc['id']}" for fc in cfg.FIXED_COSTS if fc["id"] != "leie_anlegg"] +
                      ["kr_faste_totalt"])
    fast_sum = fast.groupby("periode", sort=True)[fast_kolonner].sum().reset_index()

    out = variabel.merge(matchet_sum, on="periode", how="left").merge(fast_sum, on="periode", how="left").fillna(0.0)
    out["bruttofortjeneste_kr"] = out["inntekt_kr"] - out["varekostnad_kr"]

    # "13.2 Oppankring" er INKLUDERT her - hele terminbeløpet, ingen
    # renter/avdrag-splitt (se docstring - det er Tridents lån, ikke
    # oppdretterens, og dermed ren opex for oppdretter).
    # HELE leien 13.1-13.10 (INKL. 13.7/13.8 lønn lokalitet/land - det er
    # utleiers folk, fakturert som del av leien, ikke oppdretters egne
    # ansatte - BEKREFTET av bruker) = "andre_driftskostnader_kr" = BIG DIPPER
    # LEIE (infrastrukturkostnader) - lik 13. Leie i Konsolidert kontantstrøm
    # og leieinntekten hos utleier. 14 (brønnbåt), 15 (ADK) og 16 (adm.) er
    # oppdretters EGNE faste kostnader, egne linjer under leien.
    # Lønnskostnader = kun oppdretters egne (6 og 11, matchet).
    andre_kolonner = [f"kr_{sl['id']}" for sl in sublinjer]
    out["andre_driftskostnader_kr"] = out[[k for k in andre_kolonner if k in out.columns]].sum(axis=1)
    egne_faste = [f"kr_{fc['id']}" for fc in cfg.FIXED_COSTS if fc["id"] != "leie_anlegg" and f"kr_{fc['id']}" in out.columns]
    out["ovrige_faste_kr"] = out[egne_faste].sum(axis=1)
    lonn_leie_kolonner = []

    # ---- EBITDA -> EBIT -> resultat før skatt -> skatt -> resultat etter skatt ----
    # Avskrivninger og finanskostnader er OPPDRETTERS EGNE (ikke anleggets -
    # selve anlegget eies av utleier og betales via 13. Leie, som allerede
    # ligger i "Andre driftskostnader" over). Begge er flate NOK/år fra
    # cfg (ingen eskalering), fordelt per uke (/52) og summert per periode.
    # Skatt: sats x positivt resultat, med FREMFØRBART UNDERSKUDD - underskudd
    # i tidligere perioder trekkes fra før skatt beregnes. Beregnes
    # sekvensielt i periode-rekkefølge, så uke/måned/år gir samme sum.
    out["ebitda_kr"] = out["bruttofortjeneste_kr"] - out["lonnskostnader_kr"] - out["andre_driftskostnader_kr"] - out["ovrige_faste_kr"]

    avskr_ar = float(getattr(cfg, "AVSKRIVNINGER_KR_PER_AR", 0.0) or 0.0)
    finans_ar = float(getattr(cfg, "FINANSKOSTNADER_KR_PER_AR", 0.0) or 0.0)
    skattesats = float(getattr(cfg, "SKATTESATS", 0.22) or 0.0)
    uker_per_periode = fast.groupby("periode")["uke"].nunique()
    out = out.sort_values("periode").reset_index(drop=True)
    n_uker = out["periode"].map(uker_per_periode).fillna(0.0).astype(float)
    out["avskrivninger_kr"] = n_uker * avskr_ar / 52.0
    out["finanskostnader_kr"] = n_uker * finans_ar / 52.0
    # KONSOLIDERT: avskrivninger og renter fra eierens egen ukeplan (CAPEX,
    # vedlikeholdsinvesteringer, banklån) kommer I TILLEGG til de flate.
    eier = getattr(cfg, "EIER_UKE", None)
    if eier is not None and len(eier):
        e = eier.copy()
        e["periode"] = e["uke"].map(dict(zip(fast["uke"], fast["periode"])))
        e_sum = e.dropna(subset=["periode"]).groupby("periode", sort=True)[["avskrivning_kr", "renter_kr"]].sum()
        out["avskrivninger_kr"] = out["avskrivninger_kr"] + out["periode"].map(e_sum["avskrivning_kr"]).fillna(0.0)
        out["finanskostnader_kr"] = out["finanskostnader_kr"] + out["periode"].map(e_sum["renter_kr"]).fillna(0.0)
    out["ebit_kr"] = out["ebitda_kr"] - out["avskrivninger_kr"]
    out["resultat_for_skatt_kr"] = out["ebit_kr"] - out["finanskostnader_kr"]

    # Skatt beregnes ALLTID på ÅRSBASIS (slik selskapsskatt faktisk
    # fastsettes) - så uke/måned/år gir identisk sum. For uke/måned
    # fordeles årets skatt på periodene i året proporsjonalt med deres
    # positive resultat før skatt (perioder med underskudd får 0).
    if period == "ar":
        out["_ar"] = out["periode"].astype(int)
    else:
        ar_per_periode = (fast.assign(_ar=pd.to_datetime(fast["dato"]).dt.isocalendar().year)
                          .groupby("periode")["_ar"].first())
        _ar = out["periode"].map(ar_per_periode)
        # Fallback for perioder uten rad i fast (f.eks. leveringsuker utenfor
        # fastkost-horisonten): år fra selve periode-etiketten ("2026-U05"/"2026-01")
        _ar = _ar.astype("float").fillna(out["periode"].astype(str).str[:4].astype(float))
        out["_ar"] = _ar.astype(int)
    resultat_per_ar = out.groupby("_ar", sort=True)["resultat_for_skatt_kr"].sum()
    skatt_per_ar, fremforbart = {}, 0.0
    for ar, r in resultat_per_ar.items():
        grunnlag = r - fremforbart
        if grunnlag > 0:
            skatt_per_ar[ar] = grunnlag * skattesats
            fremforbart = 0.0
        else:
            skatt_per_ar[ar] = 0.0
            fremforbart = -grunnlag  # akkumulert underskudd til fremføring
    if period == "ar":
        out["skatt_kr"] = out["_ar"].map(skatt_per_ar).fillna(0.0)
    else:
        pos = out["resultat_for_skatt_kr"].clip(lower=0.0)
        pos_per_ar = pos.groupby(out["_ar"]).transform("sum")
        out["skatt_kr"] = (pos / pos_per_ar.replace(0.0, float("nan")) * out["_ar"].map(skatt_per_ar)).fillna(0.0)
    out = out.drop(columns=["_ar"])
    out["resultat_etter_skatt_kr"] = out["resultat_for_skatt_kr"] - out["skatt_kr"]
    # Bakoverkompatibelt navn (brukt i eldre visninger): = EBITDA før
    # avskrivninger/finans - beholdes så ingenting knekker.
    out["arsresultat_for_skatt_kr"] = out["ebitda_kr"]

    # ---- Oppstilling med ressurslinjene 0-12 (matchet) under Varekostnad/
    # Lønnskostnader, og "per solgt kg" (HOG/WFE, som i Konsolidert
    # kontantstrøm) for HVER rad - kolonnenavn "<x>__per_kg_solgt". ----
    vare_linjer = [f"kr_{rid}_matchet" for rid in resource_ider if rid not in lonns_ider]
    lonn_linjer = [f"kr_{rid}_matchet" for rid in resource_ider if rid in lonns_ider]
    andre_spes = [k for k in andre_kolonner if k in out.columns]   # 13.1-13.10 spesifisert
    keep = (["periode", "kg_solgt", "inntekt_kr"] + vare_linjer + ["varekostnad_kr", "bruttofortjeneste_kr"]
            + lonn_linjer + [k for k in lonn_leie_kolonner if k in out.columns]
            + ["lonnskostnader_kr"] + andre_spes + ["andre_driftskostnader_kr"] + egne_faste + ["ebitda_kr",
                             "avskrivninger_kr", "ebit_kr", "finanskostnader_kr", "resultat_for_skatt_kr",
                             "skatt_kr", "resultat_etter_skatt_kr"])
    out = out[keep].copy()
    kg = out["kg_solgt"].replace(0.0, float("nan"))
    ordnet = ["periode", "kg_solgt"]
    for col in keep[2:]:
        ordnet.append(col)
        out[f"{col}__per_kg_solgt"] = (out[col] / kg).fillna(0.0)
        ordnet.append(f"{col}__per_kg_solgt")
    return out[ordnet]


def build_batch_ukentlig_kostnad(cfg, ledger: pd.DataFrame, generations: dict) -> pd.DataFrame:
    """KJERNEFUNKSJON: én rad per (kohort, batch, vekstuke) - batchens andel
    av DEN UKENS ressurskostnad (0-12), UKEAVHENGIG fordelt KUN på batcher
    som IKKE ER HØSTET ENNÅ den uken (batchens delivery_week >= uken) -
    renormalisert til å summere 1,0 blant nettopp de gjenværende. En batch
    som allerede er solgt har ikke lenger fisk i tanken, og skal derfor
    ikke få noen andel av senere ukers kostnad.

    To fordelingsnøkler blant de gjenværende, per ressurslinje:
      - kilde "smolt" (0. Kjøpt smolt): etter andel av gjenværende
        batchers LEVERTE ANTALL (smoltpris er kr/stk, batchene er
        "jevnstore" i antall - se _split_i_batcher() i scheduler_1tank.py).
      - Alle andre (kilde "feed"/"wfe"): etter andel av gjenværende
        batchers LEVERTE BIOMASSE (faktisk priset mot kg WFE).

    Dette er FELLES grunnlag for:
      - Ukentlig kontantstrøm-fordeling per batch (Konsolidert kontantstrøm
        sitt Excel-outline-nedtrekk).
      - MATCHET kostnad per batch (build_matchet_kostnad_per_uke() under) -
        en batchs matchede kostnad er ganske enkelt SUMMEN av dens egne
        rader her (siden den naturlig slutter å få nye rader etter sin
        egen leveringsuke).
      - Isolert per-batch Resultatregnskap/Balanse (build_batch_resultat_
        og_balanse()).
      - Isolert utvikling-projeksjonen (build_isolert_batch_projeksjon(),
        via build_batch_ukentlig_mengde() under - samme kjerne, bare på
        FYSISK MENGDE i stedet for kr, siden den funksjonen selv påfører
        pris/eskalering i etterkant).
    Én kilde til sannhet - garanterer at alle FIRE alltid er konsistente
    med hverandre, i stedet for fire parallelle (og historisk sett litt
    ulike) implementasjoner av samme idé."""
    resource_ider = [r["id"] for r in cfg.RESOURCES]
    kr_cols = [f"kr_{rid}" for rid in resource_ider]
    return _build_batch_ukentlig_verdier(cfg, ledger, generations, resource_ider, kr_cols)


def build_batch_ukentlig_mengde(cfg, ledger: pd.DataFrame, generations: dict) -> pd.DataFrame:
    """Som build_batch_ukentlig_kostnad(), men for FYSISK MENGDE
    (mengde_<id> - stk/kg/kWh osv.) i stedet for kr - samme uke-avhengige
    fordelingsprinsipp (kun gjenværende batcher, antall for smolt/biomasse
    for resten). Brukes av build_isolert_batch_projeksjon() for å hente
    batchens egen andel av kohortens FYSISKE ressursforbruk, uavhengig av
    pris/eskalering (som først påføres etterpå, år for år, i den
    funksjonen)."""
    resource_ider = [r["id"] for r in cfg.RESOURCES]
    mengde_cols = [f"mengde_{rid}" for rid in resource_ider]
    return _build_batch_ukentlig_verdier(cfg, ledger, generations, resource_ider, mengde_cols)


def _build_batch_ukentlig_verdier(cfg, ledger: pd.DataFrame, generations: dict,
                                   resource_ider: list, verdi_cols: list) -> pd.DataFrame:
    """Delt implementasjon bak build_batch_ukentlig_kostnad()/
    build_batch_ukentlig_mengde() - se disse for fordelingsprinsippet.
    `verdi_cols` er enten kr_<id>-kolonnene eller mengde_<id>-kolonnene,
    i samme rekkefølge som `resource_ider`."""
    smolt_ider = {r["id"] for r in cfg.RESOURCES if r["kilde"] == "smolt"}

    rows = []
    for gid, info in generations.items():
        kohort_df = ledger[ledger["kohort_id"].isin([gid, f"({gid})"])]
        vekst_df = kohort_df[kohort_df["fase"] == "Vekst"].sort_values("dato").reset_index(drop=True)
        start_week = info["start_week"]

        for i, row in vekst_df.iterrows():
            wk = start_week + i
            remaining = [b for b in info["batches"] if b["delivery_week"] >= wk]
            if not remaining:
                continue
            total_biomasse_rem = sum(b["delivered_biomass_kg"] for b in remaining)
            total_antall_rem = sum(b["delivered_count"] for b in remaining)
            for b in remaining:
                andel_biomasse = (b["delivered_biomass_kg"] / total_biomasse_rem) if total_biomasse_rem else 0.0
                andel_antall = (b["delivered_count"] / total_antall_rem) if total_antall_rem else 0.0
                verdier = {"kohort_id": gid, "batch_id": b["batch_id"], "uke": row["uke"],
                           "uke_idx": wk, "dato": row["dato"]}
                for rid, col in zip(resource_ider, verdi_cols):
                    v = row.get(col)
                    v = float(v) if pd.notna(v) else 0.0
                    andel = andel_antall if rid in smolt_ider else andel_biomasse
                    verdier[col] = v * andel
                rows.append(verdier)

    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["kohort_id", "batch_id", "uke", "uke_idx", "dato"] + verdi_cols)


def build_matchet_kostnad_per_uke(cfg, ledger: pd.DataFrame, generations: dict) -> pd.DataFrame:
    """Fordeler HVER kohorts totale ressurskostnader (0-12) ut på batchene
    den til slutt splittes i - og bokfører denne andelen i NØYAKTIG den
    uken batchen faktisk LEVERES, ikke ukene kostnaden løp under vekst.

    TO fordelingsnøkler, bevisst (se _build_kontantstrom_breakdown() i
    streamlit_app_1tank.py for samme prinsipp, ukentlig kontantstrøm-
    varianten):
      - "0. Kjøpt smolt" (kilde "smolt"): etter batchens andel av
        kohortens totalt LEVERTE ANTALL fisk - smoltpris er kr/stk, og
        batchene er ved design "jevnstore" i ANTALL, ikke i biomasse.
      - Alle andre linjer (kilde "feed"/"wfe"): etter batchens andel av
        kohortens totalt LEVERTE BIOMASSE.
    Nevneren er SUMMEN av batchenes egne delivered_biomass_kg/
    delivered_count - IKKE info["delivered_biomass_kg"] (kohortens
    "ubeskattede" totalvekt ved full syklus-slutt, som er et LITT annet,
    STØRRE tall enn summen av det som faktisk ble høstet ut batch for
    batch - se scheduler_1tank.py). Feil nevner her var tidligere årsaken
    til at "Biologisk eiendel" i balansen ikke gikk helt i null ved
    levering (et par prosent restverdi ble hengende igjen for hver
    kohort) - se build_balanse().

    Dette ER "matching"-prinsippet i regnskapsføring (kostnad og tilhørende
    inntekt i SAMME periode) - uten det ville "Biologisk eiendel" i
    balansen aldri gå i null når en kohort er ferdig levert, og
    balanseligningen (eiendeler = gjeld + egenkapital) ville ikke gå opp.

    Returnerer én rad per (kohort, batch, leveringsuke), med kolonner
    "kr_<id>_matchet" for hver ressurslinje 0-12 - klar til å summeres per
    kalenderuke/måned/år for resultatregnskap og balanse."""
def build_matchet_kostnad_per_uke(cfg, ledger: pd.DataFrame, generations: dict) -> pd.DataFrame:
    """Fordeler HVER kohorts ressurskostnader (0-12) ut på batchene den til
    slutt splittes i, og bokfører hver batchs andel i NØYAKTIG den uken
    batchen faktisk LEVERES, ikke ukene kostnaden løp under vekst.

    En batchs matchede kostnad er ganske enkelt SUMMEN av dens egne
    ukentlige rader fra build_batch_ukentlig_kostnad() - se den for
    fordelingsprinsippet (uke-avhengig, kun gjenværende batcher, antall for
    smolt/biomasse for resten). Siden en batch naturlig slutter å få nye
    rader der etter sin egen leveringsuke, summerer dette automatisk KUN
    kostnaden batchen faktisk var "til stede" for å ha ansvar for - IKKE
    kostnad som løp etter at batchen allerede var solgt (en tidligere
    versjon av denne funksjonen brukte kohortens HELE, fulle syklus-
    kostnad som grunnlag for alle batchene likt, noe som feilaktig ga
    tidlig-solgte batcher (f.eks. B1) en andel av kostnader som løp etter
    at DERES fisk allerede var levert).

    Dette ER "matching"-prinsippet i regnskapsføring (kostnad og tilhørende
    inntekt i SAMME periode) - uten det ville "Biologisk eiendel" i
    balansen aldri gå i null når en kohort er ferdig levert, og
    balanseligningen (eiendeler = gjeld + egenkapital) ville ikke gå opp.

    Returnerer én rad per (kohort, batch, leveringsuke), med kolonner
    "kr_<id>_matchet" for hver ressurslinje 0-12 - klar til å summeres per
    kalenderuke/måned/år for resultatregnskap og balanse."""
    resource_ider = [r["id"] for r in cfg.RESOURCES]
    kr_cols = [f"kr_{rid}" for rid in resource_ider]

    batch_ukentlig = build_batch_ukentlig_kostnad(cfg, ledger, generations)
    agg = (batch_ukentlig.groupby(["kohort_id", "batch_id"], sort=False)[kr_cols].sum()
           if len(batch_ukentlig) else pd.DataFrame(columns=kr_cols))

    rows = []
    for gid, info in generations.items():
        for batch in info["batches"]:
            row = {"kohort_id": gid, "batch_id": batch["batch_id"], "uke_idx": batch["delivery_week"]}
            if (gid, batch["batch_id"]) in agg.index:
                sums = agg.loc[(gid, batch["batch_id"])]
                for rid, col in zip(resource_ider, kr_cols):
                    row[f"{col}_matchet"] = float(sums[col])
            else:
                for col in kr_cols:
                    row[f"{col}_matchet"] = 0.0
            rows.append(row)
    return pd.DataFrame(rows)


def build_balanse(cfg, ledger: pd.DataFrame, cashflow: pd.DataFrame, generations: dict,
                   fixed_costs_weekly: pd.DataFrame, matchet_kostnad: pd.DataFrame,
                   start_year: int, start_week: int,
                   kundefrist_uker: int, leverandorfrist_uker: int,
                   apningskontanter: float = 0.0, apningsegenkapital: float = 0.0) -> pd.DataFrame:
    """Konsolidert BALANSE for OPPDRETTER (Bremnes Seashore), én rad per
    uke, hele simuleringsperioden.

    VIKTIG: "13.2 Oppankring" er Tridents (SFaaS-leverandørens) EGET lån,
    IKKE oppdretterens - det er derfor INGEN lånesaldo, INGEN renter/avdrag-
    splitt noe sted i denne balansen. Hele oppankrings-terminbeløpet er
    REN OPEX for oppdretter, betalt kontant på linje med de andre 13.x-
    linjene - akkurat som om det var en fast serviceavgift til Trident.

    Eiendeler:
      - Kontanter: åpningskontanter + løpende netto kontantstrøm, MED
        betalingsutsettelse - kunder betaler `kundefrist_uker` uker etter
        LEVERING (ikke samme uke), leverandører betales `leverandorfrist_uker`
        uker etter at kostnaden PÅLØPER. Faste kostnader (13-16, inkl. hele
        oppankrings-terminbeløpet) betales KONTANT samme uke, ingen utsettelse.
      - Kundefordringer: ubetalt inntekt de siste `kundefrist_uker` ukene.
      - Biologisk eiendel: kumulert PÅLØPT ressurskostnad (0-12, hele
        historien) MINUS kumulert MATCHET (levert) kostnad - altså verdien
        av det som fortsatt står og vokser i tanken, til akkumulert kost.
        Går i null når en kohort er helt utlevert, per konstruksjon.

    Gjeld:
      - Leverandørgjeld: ubetalt kostnad de siste `leverandorfrist_uker` ukene.

    Egenkapital:
      - Opptjent egenkapital: åpningsegenkapital + kumulert (UKENTLIG)
        resultat fra det MATCHEDE resultatregnskapet (kostnad bokført ved
        levering, HELE 13.2 som opex - se build_resultatregnskap()).

    NB: "kundefrist_uker"/"leverandorfrist_uker" avrundes til nærmeste hele
    uke (14 dager = 2 uker, 7 dager = 1 uke) - modellen er ukebasert."""
    from scheduler_1tank import week_label

    alle_uker = list(dict.fromkeys(ledger.sort_values("dato")["uke"]))
    uke_til_dato = dict(zip(ledger["uke"], ledger["dato"]))

    # ---- Kostnad PÅLØPT per uke (0-12, hele kohorten, ALLE vekstuker) ----
    resource_ider = [r["id"] for r in cfg.RESOURCES]
    palopt = ledger.groupby("uke", sort=False)[[f"kr_{rid}" for rid in resource_ider]].sum()
    palopt["kostnad_palopt_totalt"] = palopt.sum(axis=1)
    palopt = palopt.reindex(alle_uker).fillna(0.0)

    # ---- Kostnad MATCHET (levert) per uke - fra build_matchet_kostnad_per_uke() ----
    matchet_uke_label = {}
    for _, rad in matchet_kostnad.iterrows():
        lbl, _ = week_label(rad["uke_idx"], start_year, start_week)
        matchet_uke_label[lbl] = matchet_uke_label.get(lbl, 0.0) + sum(rad[f"kr_{rid}_matchet"] for rid in resource_ider)
    matchet_serie = pd.Series({u: matchet_uke_label.get(u, 0.0) for u in alle_uker})

    # ---- Inntekt per uke (allerede levering-matchet, ingen endring nødvendig) ----
    inntekt_uke = cashflow.groupby("uke", sort=False)["inntekt_kr"].sum().reindex(alle_uker).fillna(0.0)

    # ---- Faste kostnader per uke (uendret - betales kontant samme uke,
    #      HELE 13.2 Oppankring inkludert som ren opex) ----
    fast_uke = fixed_costs_weekly.set_index("uke")["kr_faste_totalt"].reindex(alle_uker).fillna(0.0)

    # ---- Betalingsforskjøvet kontantstrøm - INGEN låneopptak å bokføre,
    #      siden lånet tilhører Trident, ikke oppdretter ----
    innbetalt = inntekt_uke.shift(kundefrist_uker, fill_value=0.0)
    utbetalt_variabel = palopt["kostnad_palopt_totalt"].shift(leverandorfrist_uker, fill_value=0.0)
    netto_kontant_uke = innbetalt - utbetalt_variabel - fast_uke
    # KONSOLIDERT: eierens egne poster (CAPEX ut, lån inn, EK-innskudd inn,
    # renter/avdrag/vedlikehold ut) i kontanter; anlegg og lån i balansen.
    eier = getattr(cfg, "EIER_UKE", None)
    if eier is not None and len(eier):
        e = eier.set_index("uke").reindex(alle_uker)
        # beholdningskolonner (bokført anlegg, lånesaldo) føres videre i uker
        # utenfor eier-planen (f.eks. siste uke i balansen) - flows = 0 der
        for _c in ("anlegg_bokfort_kr", "lan_saldo_kr"):
            e[_c] = e[_c].ffill()
        e = e.fillna(0.0)
        eier_kontant = (e["lan_opptak_kr"] + e["ek_innskudd_kr"] - e["capex_kr"] - e["vedlikehold_kr"]
                        - e["renter_kr"] - e["avdrag_kr"])
        netto_kontant_uke = netto_kontant_uke + eier_kontant.values
        anlegg_serie = pd.Series(e["anlegg_bokfort_kr"].values, index=alle_uker)
        lan_serie = pd.Series(e["lan_saldo_kr"].values, index=alle_uker)
        innskutt_serie = pd.Series(e["ek_innskudd_kr"].cumsum().values, index=alle_uker)
        eier_resultat_uke = pd.Series((-(e["avskrivning_kr"] + e["renter_kr"])).values, index=alle_uker)
    else:
        anlegg_serie = lan_serie = innskutt_serie = pd.Series(0.0, index=alle_uker)
        eier_resultat_uke = pd.Series(0.0, index=alle_uker)
    # Operasjonelt EK-innskudd (fra appen, to-trinns beregning): inn i
    # kontanter OG innskutt egenkapital fra uke 1.
    ek_oper = float(getattr(cfg, "EK_OPERASJONELT_KR", 0.0) or 0.0)
    kontanter = apningskontanter + ek_oper + netto_kontant_uke.cumsum()
    innskutt_serie = innskutt_serie + ek_oper

    # ---- Kundefordringer / leverandørgjeld (rullerende N-ukers vindu) ----
    kundefordringer = inntekt_uke.rolling(window=max(kundefrist_uker, 1), min_periods=1).sum() if kundefrist_uker > 0 else pd.Series(0.0, index=alle_uker)
    leverandorgjeld = palopt["kostnad_palopt_totalt"].rolling(window=max(leverandorfrist_uker, 1), min_periods=1).sum() if leverandorfrist_uker > 0 else pd.Series(0.0, index=alle_uker)

    # ---- Biologisk eiendel (kumulert påløpt minus kumulert matchet) ----
    biologisk_eiendel = (palopt["kostnad_palopt_totalt"].cumsum() - matchet_serie.cumsum()).clip(lower=0)

    # ---- Resultat (matchet) per uke - for opptjent egenkapital. HELE
    #      fast_uke (inkl. 13.2) belastes direkte - ingen unntak lenger. ----
    lonns_ider = {"annet_direkte_lonn", "indirekte_lonn"}
    matchet_lonn, matchet_andre = {}, {}
    for _, rad in matchet_kostnad.iterrows():
        lbl, _ = week_label(rad["uke_idx"], start_year, start_week)
        lonn_del = sum(rad[f"kr_{rid}_matchet"] for rid in resource_ider if rid in lonns_ider)
        andre_del = sum(rad[f"kr_{rid}_matchet"] for rid in resource_ider if rid not in lonns_ider)
        matchet_lonn[lbl] = matchet_lonn.get(lbl, 0.0) + lonn_del
        matchet_andre[lbl] = matchet_andre.get(lbl, 0.0) + andre_del
    varekost_matchet_serie = pd.Series({u: matchet_andre.get(u, 0.0) for u in alle_uker})
    lonn_matchet_serie = pd.Series({u: matchet_lonn.get(u, 0.0) for u in alle_uker})

    resultat_uke = inntekt_uke - varekost_matchet_serie - lonn_matchet_serie - fast_uke + eier_resultat_uke.values
    opptjent_egenkapital = apningsegenkapital + resultat_uke.cumsum()

    out = pd.DataFrame({
        "uke": alle_uker,
        "dato": [uke_til_dato[u] for u in alle_uker],
        "kontanter": kontanter.values,
        "kundefordringer": kundefordringer.values,
        "biologisk_eiendel": biologisk_eiendel.values,
        "anlegg": anlegg_serie.values,
        "leverandorgjeld": leverandorgjeld.values,
        "banklan": lan_serie.values,
        "innskutt_egenkapital": innskutt_serie.values,
        "opptjent_egenkapital": opptjent_egenkapital.values,
    })
    out["sum_eiendeler"] = out["kontanter"] + out["kundefordringer"] + out["biologisk_eiendel"] + out["anlegg"]
    out["sum_gjeld_og_egenkapital"] = out["leverandorgjeld"] + out["banklan"] + out["innskutt_egenkapital"] + out["opptjent_egenkapital"]
    out["differanse"] = out["sum_eiendeler"] - out["sum_gjeld_og_egenkapital"]
    return out


def build_batch_resultat_og_balanse(cfg, ledger: pd.DataFrame, cashflow: pd.DataFrame, generations: dict,
                                     kohort_id: str, batch_id: str, start_year: int, start_week: int,
                                     kundefrist_uker: int, leverandorfrist_uker: int) -> tuple:
    """Bygger et EGET, isolert Resultatregnskap OG Balanse for ÉN BATCH -
    INGEN faste kostnader (13-16) inkludert (de er anleggsnivå/semi-
    variable, ikke batch-nivå - "13.5 Desinfeksjon" er et godt eksempel:
    avhenger av antall kohorter i perioden, ikke av hvilken batch man ser
    på isolert).

    Kostnadene (0-12) allokeres proporsjonalt etter batchens andel av
    kohortens leverte biomasse - MEN, i motsetning til
    build_matchet_kostnad_per_uke() (som kun legger HELE andelen i
    leveringsuken), fordeles den ukentlige PÅLØPTE kostnaden ut over ALLE
    kohortens vekstuker (batchens andel av HVER ukes kostnad), slik at
    batchens EGEN biologiske eiendel bygger seg gradvis opp under vekst -
    og tømmes helt i akkurat DENNE batchens leveringsuke (der hele den
    opparbeidede andelen MATCHES mot batchens levering).

    Balanseligningen (eiendeler = gjeld + egenkapital) går opp av samme
    grunn som for den konsoliderte balansen - se build_balanse().

    Returnerer (resultat, balanse, kontantstrom) - tre DataFrames, alle
    uke for uke. "kontantstrom" viser den FAKTISKE kontantbevegelsen
    (påløpt inntekt/kostnad MED betalingsutsettelse), til forskjell fra
    "resultat" (som bruker MATCHET kostnad ved levering) - de to henger
    sammen via "balanse" sin "kontanter"-linje."""
    from scheduler_1tank import week_label

    info = generations[kohort_id]
    batch = next(b for b in info["batches"] if b["batch_id"] == batch_id)

    kohort_uker_df = ledger[ledger["kohort_id"].isin([kohort_id, f"({kohort_id})"])].sort_values("dato")
    alle_uker = list(kohort_uker_df["uke"])
    uke_til_dato = dict(zip(kohort_uker_df["uke"], kohort_uker_df["dato"]))

    resource_ider = [r["id"] for r in cfg.RESOURCES]
    lonns_ider = {"annet_direkte_lonn", "indirekte_lonn"}
    varekost_ider = [rid for rid in resource_ider if rid not in lonns_ider]
    lonn_ider_alle = [rid for rid in resource_ider if rid in lonns_ider]

    # Per RESSURSLINJE (0-12) - hentes fra build_batch_ukentlig_kostnad(),
    # SAMME kjernefunksjon som driver batch-nedtrekket i Konsolidert
    # kontantstrøm og den matchede kostnaden i Resultatregnskap/Balanse -
    # garanterer at denne isolerte per-batch-visningen alltid er konsistent
    # med de konsoliderte visningene (uke-avhengig: en batch som allerede
    # er høstet ut får automatisk 0 kr i alle senere uker).
    batch_ukentlig = build_batch_ukentlig_kostnad(cfg, ledger, {kohort_id: info})
    bd = batch_ukentlig[batch_ukentlig["batch_id"] == batch_id].set_index("uke") if len(batch_ukentlig) else pd.DataFrame()
    palopt_per_linje_uke = {}
    for rid in resource_ider:
        col = f"kr_{rid}"
        palopt_per_linje_uke[rid] = (bd[col].reindex(alle_uker).fillna(0.0) if col in bd.columns
                                      else pd.Series(0.0, index=alle_uker))

    varekost_palopt_uke = sum((palopt_per_linje_uke[rid] for rid in varekost_ider), pd.Series(0.0, index=alle_uker))
    lonn_palopt_uke = (sum((palopt_per_linje_uke[rid] for rid in lonn_ider_alle), pd.Series(0.0, index=alle_uker))
                       if lonn_ider_alle else pd.Series(0.0, index=alle_uker))

    lbl_levering, _ = week_label(batch["delivery_week"], start_year, start_week)

    # Batchen slutter å ta til seg NY allokert kostnad ETTER SIN EGEN
    # leveringsuke - den er jo ikke lenger i tanken (fisken er solgt).
    # Uten dette ville "biologisk eiendel" fortsatt vokse for en batch som
    # allerede er levert (siden andelen ellers ville fortsette å løpe helt
    # til KOHORTENS aller siste batch er ferdig) - og balansen ville da
    # IKKE gå opp for noen annen batch enn den siste i kohorten.
    if lbl_levering in alle_uker:
        levering_idx = alle_uker.index(lbl_levering)
        for u in alle_uker[levering_idx + 1:]:
            varekost_palopt_uke[u] = 0.0
            lonn_palopt_uke[u] = 0.0
            for rid in resource_ider:
                palopt_per_linje_uke[rid][u] = 0.0
    palopt_totalt_uke = varekost_palopt_uke + lonn_palopt_uke

    inntekt_uke = pd.Series(0.0, index=alle_uker)
    if lbl_levering in inntekt_uke.index:
        batch_rad = cashflow[(cashflow["kohort_id"] == kohort_id) & (cashflow["batch_id"] == batch_id) &
                              (cashflow["kg_wfe_levert"] > 0)]
        inntekt_uke.loc[lbl_levering] = batch_rad["inntekt_kr"].sum()

    # MATCHET kostnad - HELE batchens opparbeidede andel bokføres i EGEN leveringsuke
    matchet_varekost_uke = pd.Series(0.0, index=alle_uker)
    matchet_lonn_uke = pd.Series(0.0, index=alle_uker)
    if lbl_levering in matchet_varekost_uke.index:
        matchet_varekost_uke.loc[lbl_levering] = varekost_palopt_uke.sum()
        matchet_lonn_uke.loc[lbl_levering] = lonn_palopt_uke.sum()
    matchet_totalt_uke = matchet_varekost_uke + matchet_lonn_uke

    # ---- Resultatregnskap ----
    bruttofortjeneste_uke = inntekt_uke - matchet_varekost_uke
    resultat_uke = bruttofortjeneste_uke - matchet_lonn_uke
    resultat = pd.DataFrame({
        "uke": alle_uker, "dato": [uke_til_dato[u] for u in alle_uker],
        "inntekt_kr": inntekt_uke.values, "varekostnad_kr": matchet_varekost_uke.values,
        "bruttofortjeneste_kr": bruttofortjeneste_uke.values, "lonnskostnader_kr": matchet_lonn_uke.values,
        "arsresultat_for_skatt_kr": resultat_uke.values,
    })

    # ---- Kontantstrøm (uke for uke - PÅLØPT kostnad/inntekt, med betalings-
    #      utsettelse, IKKE den MATCHEDE (leverings-tidspunkt) versjonen som
    #      Resultatregnskapet bruker - kontanter beveger seg jo når regningen
    #      faktisk forfaller/betales, ikke når kostnaden regnskapsføres) ----
    innbetalt = inntekt_uke.shift(kundefrist_uker, fill_value=0.0)
    utbetalt = palopt_totalt_uke.shift(leverandorfrist_uker, fill_value=0.0)
    netto_kontant_uke = innbetalt - utbetalt

    kontantstrom_data = {
        "uke": alle_uker, "dato": [uke_til_dato[u] for u in alle_uker],
        "palopt_inntekt_kr": inntekt_uke.values,
    }
    # Påløpt kostnad, SPESIFISERT per ressurslinje (0-12)
    for r in cfg.RESOURCES:
        kontantstrom_data[f"palopt_{r['id']}_kr"] = palopt_per_linje_uke[r["id"]].values
    kontantstrom_data["palopt_kostnad_totalt_kr"] = palopt_totalt_uke.values
    kontantstrom_data["innbetalt_kr"] = innbetalt.values
    # Utbetalt til leverandører, SPESIFISERT per ressurslinje (0-12) - samme
    # forskyvning (leverandorfrist_uker) som for totalen.
    for r in cfg.RESOURCES:
        kontantstrom_data[f"utbetalt_{r['id']}_kr"] = palopt_per_linje_uke[r["id"]].shift(leverandorfrist_uker, fill_value=0.0).values
    kontantstrom_data["utbetalt_kostnad_totalt_kr"] = utbetalt.values
    kontantstrom_data["netto_kontantstrom_kr"] = netto_kontant_uke.values
    kontantstrom_data["akkumulert_kontantstrom_kr"] = netto_kontant_uke.cumsum().values
    kontantstrom = pd.DataFrame(kontantstrom_data)

    # ---- Balanse ----
    kontanter = netto_kontant_uke.cumsum()
    kundefordringer = inntekt_uke.rolling(window=max(kundefrist_uker, 1), min_periods=1).sum()
    leverandorgjeld = palopt_totalt_uke.rolling(window=max(leverandorfrist_uker, 1), min_periods=1).sum()
    biologisk_eiendel = (palopt_totalt_uke.cumsum() - matchet_totalt_uke.cumsum()).clip(lower=0)
    opptjent_egenkapital = resultat_uke.cumsum()

    balanse = pd.DataFrame({
        "uke": alle_uker, "dato": [uke_til_dato[u] for u in alle_uker],
        "kontanter": kontanter.values, "kundefordringer": kundefordringer.values,
        "biologisk_eiendel": biologisk_eiendel.values, "leverandorgjeld": leverandorgjeld.values,
        "opptjent_egenkapital": opptjent_egenkapital.values,
    })
    balanse["sum_eiendeler"] = balanse["kontanter"] + balanse["kundefordringer"] + balanse["biologisk_eiendel"]
    balanse["sum_gjeld_og_egenkapital"] = balanse["leverandorgjeld"] + balanse["opptjent_egenkapital"]
    balanse["differanse"] = balanse["sum_eiendeler"] - balanse["sum_gjeld_og_egenkapital"]

    return resultat, balanse, kontantstrom


def build_isolert_batch_projeksjon(cfg, ledger: pd.DataFrame, generations: dict, fixed_costs_weekly: pd.DataFrame,
                                    kohort_id: str, batch_id: str, years: list, sales_price_kr_per_kg: float,
                                    hog_faktor: float = 1.0, seasonal_index_by_week: dict | None = None,
                                    sales_price_table: list | None = None) -> pd.DataFrame:
    """Fremskriver "hva om HELE driften bare besto av denne ene batchen,
    hvert år, escalert normalt" - IKKE en historisk visning av batchens
    egne faktiske uker (den finnes jo bare i ÉTT faktisk kalenderår), men
    en HYPOTETISK projeksjon over hele simuleringens årsrekke.

    Fysisk volum (kg WFE bruttovekst, fôr kg, smoltantall osv.) for batchen
    hentes fra build_batch_ukentlig_mengde() - SAMME uke-avhengige kjerne
    (kun gjenværende, ikke-høstede batcher i hver uke) som driver batch-
    nedtrekket i Konsolidert kontantstrøm og Resultatregnskap/Balanse.
    Batchens totale allokerte fysiske volum er ganske enkelt SUMMEN av
    dens egne ukentlige rader der (stopper naturlig ved egen leveringsuke -
    en tidligere versjon brukte i stedet en FAST andel påført kohortens
    HELE, fulle syklus-forbruk, som feilaktig ga tidlig-solgte batcher
    (f.eks. B1) et volum som inkluderte ressursbruk fra uker ETTER at
    DERES fisk allerede var solgt).

    For HVERT år i `years` beregnes deretter kr på nytt fra dette FASTE
    fysiske volumet, med DET ÅRETS eskalerte pris (samme prinsipp som
    resten av modellen - se escalate_price_by_year()).

    INGEN faste kostnader (13-16) er med her - de er anleggsnivå/semi-
    variable, ikke batch-nivå (samme prinsipp som Resultatregnskap/Balanse
    per batch - se build_batch_resultat_og_balanse()). "Netto kontantstrøm"
    er derfor lik "Dekningsbidrag" her (ingen faste kostnader å trekke fra).

    `seasonal_index_by_week`: valgfri dict {ISO-ukenummer: indeks} - se
    build_cashflow_ledger(). Brukes her med batchens EGEN faktiske
    leveringsukes ISO-ukenummer, holdt FAST år for år i projeksjonen
    (batchen "gjentar seg" alltid i samme kalenderuke i denne hypotetiske
    fremskrivningen). None (default) gir uendret oppførsel (flat pris)."""
    info = generations[kohort_id]
    batch = next(b for b in info["batches"] if b["batch_id"] == batch_id)
    total_biomasse = sum(b["delivered_biomass_kg"] for b in info["batches"])

    batch_mengde = build_batch_ukentlig_mengde(cfg, ledger, {kohort_id: info})
    bm = batch_mengde[batch_mengde["batch_id"] == batch_id] if len(batch_mengde) else batch_mengde
    mengde_allokert = {}
    for r in cfg.RESOURCES:
        kol = f"mengde_{r['id']}"
        mengde_allokert[r["id"]] = float(bm[kol].sum()) if (len(bm) and kol in bm.columns) else 0.0

    # andel_biomasse beholdes KUN som returverdi ("X sin andel av leveransen"
    # vist i sidepanelet) - selve mengdeallokeringen bruker nå build_batch_
    # ukentlig_mengde() over, ikke denne andelen direkte.
    andel_biomasse = batch["delivered_biomass_kg"] / total_biomasse if total_biomasse else 0.0

    escalation_rates_by_year = getattr(cfg, "ESCALATION_RATES_BY_YEAR", {})
    escalation_base_year = getattr(cfg, "ESCALATION_BASE_YEAR", cfg.START_ISO_YEAR)
    prices = getattr(cfg, "RESOURCE_PRICES_NOK", {})
    batch_solgt_kg = batch["delivered_biomass_kg"] * hog_faktor

    batch_leverings_uke = None
    if seasonal_index_by_week:
        batch_uke_rad = ledger[(ledger["kohort_id"] == kohort_id) & (ledger["batch_id"] == batch_id)]
        if len(batch_uke_rad):
            batch_leverings_uke = int(pd.to_datetime(batch_uke_rad["dato"].iloc[-1]).isocalendar()[1])

    rows = []
    for y in years:
        row = {"periode": y}
        kostnad_variabel_totalt = 0.0
        for r in cfg.RESOURCES:
            rate_by_year = escalation_rates_by_year.get(r["id"], {})
            pris = escalate_price_by_year(prices.get(r["id"]), rate_by_year, y, escalation_base_year)
            kr = mengde_allokert[r["id"]] * pris if pris is not None else 0.0
            row[f"kr_{r['id']}"] = kr
            kostnad_variabel_totalt += kr
        row["kostnad_variabel_totalt_kr"] = kostnad_variabel_totalt

        inntekt_rate_by_year = escalation_rates_by_year.get("inntekt", {})
        if sales_price_table:
            basispris = interpoler_fiskeverdi_kr_per_kg(batch["avg_weight_kg"] * 1000.0, sales_price_table)
        else:
            basispris = sales_price_kr_per_kg
        eskalert_salgspris = escalate_price_by_year(basispris, inntekt_rate_by_year, y, escalation_base_year)
        if seasonal_index_by_week and batch_leverings_uke is not None:
            eskalert_salgspris *= seasonal_index_by_week.get(batch_leverings_uke, 1.0)
        row["inntekt_kr"] = batch_solgt_kg * eskalert_salgspris
        row["kg_solgt"] = batch_solgt_kg
        rows.append(row)
    out = pd.DataFrame(rows)

    out["dekningsbidrag_kr"] = out["inntekt_kr"] - out["kostnad_variabel_totalt_kr"]
    out["dekningsgrad_pct"] = (out["dekningsbidrag_kr"] / out["inntekt_kr"] * 100).where(out["inntekt_kr"] > 0)
    out = out.sort_values("periode").reset_index(drop=True)
    out["akkumulert_dekningsbidrag_kr"] = out["dekningsbidrag_kr"].cumsum()

    kr_linjer_med_per_kg = ["inntekt_kr"] + [f"kr_{r['id']}" for r in cfg.RESOURCES] + ["kostnad_variabel_totalt_kr", "dekningsbidrag_kr"]
    per_kg_kolonner = {}
    for kr_kol in kr_linjer_med_per_kg:
        per_kg_kol = f"{kr_kol}__per_kg_solgt"
        out[per_kg_kol] = (out[kr_kol].astype(float) / out["kg_solgt"].astype(float)).where(out["kg_solgt"].astype(float) > 0)
        per_kg_kolonner[kr_kol] = per_kg_kol

    keep = ["periode", "inntekt_kr", per_kg_kolonner["inntekt_kr"]]
    for r in cfg.RESOURCES:
        keep += [f"kr_{r['id']}", per_kg_kolonner[f"kr_{r['id']}"]]
    keep += ["kostnad_variabel_totalt_kr", per_kg_kolonner["kostnad_variabel_totalt_kr"],
             "dekningsbidrag_kr", per_kg_kolonner["dekningsbidrag_kr"], "dekningsgrad_pct",
             "akkumulert_dekningsbidrag_kr"]
    return out[keep], andel_biomasse


def build_konsolidert_kontantstrom(cfg, cashflow: pd.DataFrame, fixed_costs_weekly: pd.DataFrame,
                                    period: str) -> pd.DataFrame:
    """Konsolidert kontantstrøm for HELE anlegget (summert på tvers av ALLE
    batcher/kohorter som er aktive i perioden - IKKE filtrert til én batch,
    siden faste kostnader per definisjon ikke kan tilordnes én enkelt
    batch). Legger de faste kostnadene (13-16, inkl. underlinjene 13.1-13.6)
    til periodens variable kostnad (0-12) for å gi en reell "Kostnad totalt
    (konsolidert)" og "Netto kontantstrøm (konsolidert)".

    `period`: "uke", "maned" eller "ar". Akkumulert kontantstrøm er her en
    LØPENDE SUM OVER HELE KALENDERTIDEN (nullstilles IKKE per kohort, i
    motsetning til den batch-spesifikke akkumulert-kolonnen ellers) - det
    er nettopp poenget med en konsolidert visning.

    Inntekts- og kostnadsindeks: periodens Inntekt/Kostnad totalt delt på
    FØRSTE periodes tilsvarende tall x 100 (første periode = indeks 100).
    Periodene uten inntekt/kostnad i det hele tatt (f.eks. før første
    kohort er levert) hopper indeksen over (vises tom) - en indeks basert
    på 0 gir ikke mening."""
    sublinjer = getattr(cfg, "HEXACAGE_LEIE_SUBLINJER", [])

    if period == "uke":
        # Summert PER UKE på tvers av alle kohorter/tanker - med flere
        # tanker parallelt (Big Dipper) har cashflow flere rader per uke, og
        # de faste kostnadene skal selvsagt bare telles ÉN gang per uke.
        variabel = summarize_cashflow_by_period(cashflow, "uke")
        fast = fixed_costs_weekly.copy()
        fast["periode"] = fast["uke"]
    elif period in ("maned", "ar"):
        variabel = summarize_cashflow_by_period(cashflow, period)
        fast = fixed_costs_weekly.copy()
        if period == "maned":
            fast["periode"] = pd.to_datetime(fast["dato"]).dt.strftime("%Y-%m")
        else:
            fast["periode"] = pd.to_datetime(fast["dato"]).dt.isocalendar().year
    else:
        raise ValueError("period ma vaere 'uke', 'maned' eller 'ar'")

    fast_kolonner = ([f"kr_{sl['id']}" for sl in sublinjer] + ["kr_leie_anlegg"] +
                      [f"kr_{fc['id']}" for fc in cfg.FIXED_COSTS if fc["id"] != "leie_anlegg"] +
                      ["kr_faste_totalt"])
    fast_sum = fast.groupby("periode", sort=True)[fast_kolonner].sum().reset_index()

    out = variabel.merge(fast_sum, on="periode", how="left").fillna(0.0)
    out = out.rename(columns={"kostnad_totalt_kr": "kostnad_variabel_totalt_kr"})
    out["kostnad_totalt_konsolidert_kr"] = out["kostnad_variabel_totalt_kr"] + out["kr_faste_totalt"]
    out["netto_kontantstrom_konsolidert_kr"] = out["inntekt_kr"] - out["kostnad_totalt_konsolidert_kr"]
    # ---- KONSOLIDERT: eierens egne kontantstrømmer (CAPEX, lån, renter,
    #      avdrag, vedlikeholdsinvesteringer, egenkapitalinnskudd) ----
    eier = getattr(cfg, "EIER_UKE", None)
    eier_kolonner = ["capex_kr", "vedlikehold_kr", "renter_kr", "avdrag_kr", "lan_opptak_kr", "ek_innskudd_kr"]
    if eier is not None and len(eier):
        e = eier.copy()
        e["periode"] = e["uke"].map(dict(zip(fast["uke"], fast["periode"])))
        e_sum = e.dropna(subset=["periode"]).groupby("periode", sort=True)[eier_kolonner].sum().reset_index()
        out = out.merge(e_sum, on="periode", how="left").fillna(0.0)
        out["eier_netto_kr"] = (out["lan_opptak_kr"] + out["ek_innskudd_kr"] - out["capex_kr"]
                                - out["vedlikehold_kr"] - out["renter_kr"] - out["avdrag_kr"])
        out["netto_kontantstrom_konsolidert_kr"] = out["netto_kontantstrom_konsolidert_kr"] + out["eier_netto_kr"]
    out = out.sort_values("periode").reset_index(drop=True)
    # Egenkapitalinnskudd for OPERASJONELT kapitalbehov (beregnet i appen i
    # første gjennomkjøring som bunnpunktet i kontantbeholdningen, uten
    # sirkularitet): legges inn i FØRSTE periode, så akkumulert kontantstrøm
    # / kontanter bunner på 0 i stedet for på minus.
    ek_oper = float(getattr(cfg, "EK_OPERASJONELT_KR", 0.0) or 0.0)
    out["ek_operasjonelt_kr"] = 0.0
    if ek_oper > 0 and len(out):
        out.loc[0, "ek_operasjonelt_kr"] = ek_oper
        out["netto_kontantstrom_konsolidert_kr"] = out["netto_kontantstrom_konsolidert_kr"] + out["ek_operasjonelt_kr"]
    out["akkumulert_kontantstrom_konsolidert_kr"] = out["netto_kontantstrom_konsolidert_kr"].cumsum()
    out["dekningsbidrag_kr"] = out["inntekt_kr"] - out["kostnad_variabel_totalt_kr"]
    out["dekningsgrad_pct"] = (out["dekningsbidrag_kr"] / out["inntekt_kr"] * 100).where(out["inntekt_kr"] > 0)

    # Indekser (første periode med reelt tall = 100)
    inntekt_base = out.loc[out["inntekt_kr"] > 0, "inntekt_kr"].iloc[0] if (out["inntekt_kr"] > 0).any() else None
    kostnad_base = out.loc[out["kostnad_totalt_konsolidert_kr"] > 0, "kostnad_totalt_konsolidert_kr"].iloc[0] \
        if (out["kostnad_totalt_konsolidert_kr"] > 0).any() else None
    out["inntektsindeks"] = (out["inntekt_kr"] / inntekt_base * 100) if inntekt_base else pd.NA
    out["kostnadsindeks"] = (out["kostnad_totalt_konsolidert_kr"] / kostnad_base * 100) if kostnad_base else pd.NA
    out.loc[out["inntekt_kr"] <= 0, "inntektsindeks"] = pd.NA
    out.loc[out["kostnad_totalt_konsolidert_kr"] <= 0, "kostnadsindeks"] = pd.NA

    # ---- Per solgt kg (HOG for slaktefisk, WFE for postsmolt) ----------
    # EN generisk "_per_kg_solgt"-følgesvenn for HVER kr-linje under - viser
    # om enhetstallet stiger eller faller med volum, uavhengig av periodens
    # totale kronebeløp. kg_solgt er allerede riktig enhet (satt i
    # build_cashflow_ledger()). Akkumulert kontantstrøm er en løpende SUM
    # over kalendertid og får derfor IKKE en per-kg-følgesvenn - å dele et
    # akkumulert kronebeløp på én periodes kg gir ikke mening.
    #
    # FORELØPIG kun på de VARIABLE linjene (inntekt + 0-12 + variabel
    # kostnad totalt) - IKKE på de faste kostnadene (13-16 m/underlinjer,
    # faste totalt, kostnad/netto totalt konsolidert) ennå.
    kr_linjer_med_per_kg = ["inntekt_kr"] + [f"kr_{r['id']}" for r in cfg.RESOURCES] + ["kostnad_variabel_totalt_kr", "dekningsbidrag_kr"]
    per_kg_kolonner = {}
    for kr_kol in kr_linjer_med_per_kg:
        per_kg_kol = f"{kr_kol}__per_kg_solgt"
        out[per_kg_kol] = (out[kr_kol].astype(float) / out["kg_solgt"].astype(float)).where(out["kg_solgt"].astype(float) > 0)
        per_kg_kolonner[kr_kol] = per_kg_kol

    # Rekkefølgen: hver kr-linje ETTERFULGT AV sin egen per-kg-følgesvenn,
    # slik at de to alltid står rett ved siden av hverandre i tabellen.
    keep = ["periode", "inntekt_kr", per_kg_kolonner["inntekt_kr"], "inntektsindeks"]
    for r in cfg.RESOURCES:
        keep += [f"kr_{r['id']}", per_kg_kolonner[f"kr_{r['id']}"]]
    keep += ["kostnad_variabel_totalt_kr", per_kg_kolonner["kostnad_variabel_totalt_kr"],
             "dekningsbidrag_kr", per_kg_kolonner["dekningsbidrag_kr"], "dekningsgrad_pct"]
    keep += ["kr_leie_anlegg"]
    for sl in sublinjer:
        keep += [f"kr_{sl['id']}"]
    for fc in cfg.FIXED_COSTS:
        if fc["id"] != "leie_anlegg":
            keep += [f"kr_{fc['id']}"]
    keep += ["kr_faste_totalt"]
    keep += ["kostnad_totalt_konsolidert_kr", "kostnadsindeks"]
    if "eier_netto_kr" in out.columns:
        keep += ["capex_kr", "vedlikehold_kr", "renter_kr", "avdrag_kr", "lan_opptak_kr", "ek_innskudd_kr", "eier_netto_kr"]
    if ek_oper > 0:
        keep += ["ek_operasjonelt_kr"]
    keep += ["netto_kontantstrom_konsolidert_kr"]
    keep += ["akkumulert_kontantstrom_konsolidert_kr"]
    return out[keep]


def build_monthly_overview(cfg, ledger: pd.DataFrame, generations: dict, all_months: list | None = None) -> pd.DataFrame:
    """Bygger datagrunnlaget for den samlede oversiktsgrafen (stående
    biomasse, MAB, akkumulert levert biomasse, smolt inn, solgt biomasse,
    fôr/oksygen/strøm) - én rad per kalendermåned. Egen funksjon, atskilt
    fra summarize_by_month(), fordi den blander inn ting (stående biomasse,
    solgt biomasse) som ikke er ressurslinjer fra cfg.RESOURCES.

    `ledger`/`generations` kan være FILTRERT (f.eks. til én oppskrift) -
    da vises bare denne oppskriftens aktive måneder med tall, resten 0.
    `all_months` (valgfri, sorterte "YYYY-MM"-strenger) sikrer i så fall at
    tidslinjen fortsatt dekker HELE simuleringsperioden (ikke bare månedene
    denne oppskriften faktisk var i tanken) - ellers ville en filtrert graf
    vist en komprimert tidslinje med hoppende måneder. Akkumulert levert
    biomasse fortsetter riktig gjennom "hullene" fordi cumsum uansett bare
    legger til 0 i månedene uten leveranse."""
    from scheduler_1tank import week_label

    base = ledger.sort_values("dato").copy()
    base["maned"] = pd.to_datetime(base["dato"]).dt.strftime("%Y-%m")

    # .max() (ikke .last()) - grafen skal vise MÅNEDENS FAKTISKE TOPPUNKT for
    # stående biomasse, ikke bare verdien siste uke i måneden. Toppen (særlig
    # rett før et salgsvindu starter) inntreffer ofte MIDT i måneden, ikke på
    # slutten - med .last() ville grafen aldri vist den reelle toppen, og
    # dermed sett ut til å ligge under MAB-linjen selv når "Maks tetthet"-
    # kolonnen i Kohort-sammendrag (som bruker det ekte ukentlige maksimumet)
    # viser at taket faktisk brytes.
    # Flere kohorter/tanker i SAMME uke (Big Dipper): summer først per uke
    # på tvers av kohortene, og ta deretter ukemaks i måneden. (.max() rett
    # på radene ga bare den STØRSTE ENKELTKOHORTEN, ikke anleggets samlede
    # biomasse - grafen lå da ~6x for lavt med seks tanker.)
    per_uke = base.groupby(["maned", "uke"], sort=True)["biomasse_kg"].sum().reset_index()
    standing = per_uke.groupby("maned", sort=True)["biomasse_kg"].max().reset_index() \
        .rename(columns={"biomasse_kg": "standing_biomass_kg"})
    smolt = base.groupby("maned")["mengde_smolt"].sum().reset_index() \
        .rename(columns={"mengde_smolt": "smolt_stocked_stk"})
    feed = base.groupby("maned")["mengde_for"].sum().reset_index() \
        .rename(columns={"mengde_for": "feed_kg"})
    energi = base.groupby("maned")["mengde_energi"].sum().reset_index() \
        .rename(columns={"mengde_energi": "energi_kwh"})
    oksygen = base.groupby("maned")["mengde_oksygen"].sum().reset_index() \
        .rename(columns={"mengde_oksygen": "oksygen_kg"})

    # Snittvekt (g) ved slutten av måneden - siste GYLDIGE (ikke-NaN) verdi,
    # siden vaskeuker har vekt_g=None (ingen fisk i tanken da). .last() alene
    # ville gitt NaN for en måned som ender i en vaskeuke - derfor dropna()
    # før vi henter siste verdi.
    # Med flere kohorter samtidig: BIOMASSEVEKTET snittvekt over kohortene
    # i månedens siste uke med fisk (én kohort = dens egen vekt, som før).
    def _siste_gyldige(df_m):
        gyldig = df_m[df_m["vekt_g"].notna() & (df_m["biomasse_kg"] > 0)]
        if not len(gyldig):
            return 0.0
        siste_uke = gyldig["uke"].iloc[-1]
        u = gyldig[gyldig["uke"] == siste_uke]
        return float((u["vekt_g"] * u["biomasse_kg"]).sum() / u["biomasse_kg"].sum())

    vekt = base.groupby("maned", sort=True)[["uke", "vekt_g", "biomasse_kg"]].apply(_siste_gyldige).reset_index() \
        .rename(columns={0: "vekt_g"})

    delivered_rows = []
    for gid, info in generations.items():
        for batch in info["batches"]:
            _, d = week_label(batch["delivery_week"], cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
            delivered_rows.append({"maned": d.strftime("%Y-%m"), "delivered_biomass_kg": batch["delivered_biomass_kg"]})
    delivered = (pd.DataFrame(delivered_rows).groupby("maned")["delivered_biomass_kg"].sum().reset_index()
                 if delivered_rows else pd.DataFrame(columns=["maned", "delivered_biomass_kg"]))

    out = standing.merge(smolt, on="maned", how="left") \
        .merge(feed, on="maned", how="left") \
        .merge(energi, on="maned", how="left") \
        .merge(oksygen, on="maned", how="left") \
        .merge(vekt, on="maned", how="left") \
        .merge(delivered, on="maned", how="left") \
        .fillna(0.0)

    if all_months is not None:
        out = out.set_index("maned").reindex(all_months).fillna(0.0).reset_index().rename(columns={"index": "maned"})
    else:
        out = out.sort_values("maned").reset_index(drop=True)

    # Akkumulert levert biomasse nullstilles hvert kalenderår (januar) -
    # "akkumulert ÅRLIG salg", ikke en løpende sum over hele simuleringen.
    out["ar"] = out["maned"].str[:4].astype(int)
    out["delivered_biomass_kg_akkumulert"] = out.groupby("ar")["delivered_biomass_kg"].cumsum()
    out = out.drop(columns=["ar"])
    return out


def summarize_by_cohort(ledger: pd.DataFrame, cfg) -> pd.DataFrame:
    """Én rad per kohort: sum av alle ressurslinjer over hele kohortens
    levetid (vekst + tilhørende vaskeuker). Utgangspunkt for et senere
    PnL-lag per batch."""
    base = ledger.copy()
    base["kohort_group"] = base["kohort_id"].str.strip("()")
    agg_cols = {
        "kg_wfe_brutto": ("kg_wfe_brutto", "sum"),
        "kg_wfe_dodelighet": ("kg_wfe_dodelighet", "sum"),
        "kg_wfe_netto": ("kg_wfe_netto", "sum"),
    }
    for r in cfg.RESOURCES:
        agg_cols[f"mengde_{r['id']}"] = (f"mengde_{r['id']}", "sum")
        agg_cols[f"kr_{r['id']}"] = (f"kr_{r['id']}", lambda s: s.sum(min_count=1))
    return base.groupby("kohort_group", sort=False).agg(**agg_cols).reset_index().rename(
        columns={"kohort_group": "kohort_id"})


def summarize_by_month(ledger: pd.DataFrame, cfg) -> pd.DataFrame:
    """Én rad per kalendermåned - alle ressurslinjer summert."""
    base = ledger.copy()
    base["maned"] = pd.to_datetime(base["dato"]).dt.strftime("%Y-%m")
    agg_cols = {
        "kg_wfe_brutto": ("kg_wfe_brutto", "sum"),
        "kg_wfe_dodelighet": ("kg_wfe_dodelighet", "sum"),
        "kg_wfe_netto": ("kg_wfe_netto", "sum"),
    }
    for r in cfg.RESOURCES:
        agg_cols[f"mengde_{r['id']}"] = (f"mengde_{r['id']}", "sum")
        agg_cols[f"kr_{r['id']}"] = (f"kr_{r['id']}", lambda s: s.sum(min_count=1))
    return base.groupby("maned", sort=True).agg(**agg_cols).reset_index()


def summarize_by_year(ledger: pd.DataFrame, cfg) -> pd.DataFrame:
    """Én rad per kalenderår - alle ressurslinjer summert, til bruk som
    årlig ressursbudsjett/-regnskap."""
    base = ledger.copy()
    base["ar"] = pd.to_datetime(base["dato"]).dt.isocalendar().year
    agg_cols = {
        "kg_wfe_brutto": ("kg_wfe_brutto", "sum"),
        "kg_wfe_dodelighet": ("kg_wfe_dodelighet", "sum"),
        "kg_wfe_netto": ("kg_wfe_netto", "sum"),
    }
    for r in cfg.RESOURCES:
        agg_cols[f"mengde_{r['id']}"] = (f"mengde_{r['id']}", "sum")
        agg_cols[f"kr_{r['id']}"] = (f"kr_{r['id']}", lambda s: s.sum(min_count=1))
    return base.groupby("ar", sort=True).agg(**agg_cols).reset_index()


# ----------------------------------------------------------------------
# Timeverk - egen, separat oversikt (operasjonell bemanningsplanlegging,
# IKKE en del av COGS-ressurslisten over - se config_1tank.py punkt 7b).
# ----------------------------------------------------------------------
def build_labor_hours(cfg, cohorts, generations) -> pd.DataFrame:
    from scheduler_1tank import week_label

    norms = cfg.TIME_NORMS
    labor_price = getattr(cfg, "LABOR_PRICE_KR_PER_TIME", None)
    rows = []
    cohort_by_id = {c.id: c for c in cohorts}

    for gid, info in generations.items():
        c = cohort_by_id[gid]
        for i in range(c.n_weeks):
            wk = c.start_week + i
            lbl, d = week_label(wk, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
            is_first_week = (i == 0)
            is_delivery_week = (i == c.n_weeks - 1)
            timer_innsett = norms["innsett_timer_per_kohort"] if is_first_week else 0.0
            timer_foring = norms["foring_timer_per_uke"]
            timer_sortering = timer_levering = 0.0
            if is_delivery_week:
                antall_levert = info["survivors_at_delivery"]
                timer_sortering = norms["sortering_timer_per_100k_fisk"] * (antall_levert / 100_000.0)
                timer_levering = norms["levering_timer_per_kohort"]
            timer_totalt = timer_innsett + timer_foring + timer_sortering + timer_levering
            rows.append({
                "kohort_id": gid, "uke": lbl, "dato": d.isoformat(), "fase": "Vekst",
                "timer_innsett": timer_innsett, "timer_foring": timer_foring,
                "timer_sortering": round(timer_sortering, 1), "timer_levering": timer_levering,
                "timer_rengjoring": 0.0, "timer_totalt": round(timer_totalt, 1),
                "timer_kr": round(timer_totalt * labor_price, 0) if labor_price is not None else None,
            })
        for k in range(info["cleaning_weeks"]):
            wk = info["delivery_week"] + 1 + k
            lbl, d = week_label(wk, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK)
            timer_rengjoring = norms["rengjoring_timer_per_uke"]
            rows.append({
                "kohort_id": f"({gid})", "uke": lbl, "dato": d.isoformat(), "fase": "Vask",
                "timer_innsett": 0.0, "timer_foring": 0.0, "timer_sortering": 0.0, "timer_levering": 0.0,
                "timer_rengjoring": timer_rengjoring, "timer_totalt": round(timer_rengjoring, 1),
                "timer_kr": round(timer_rengjoring * labor_price, 0) if labor_price is not None else None,
            })

    return pd.DataFrame(rows).sort_values(["dato", "kohort_id"]).reset_index(drop=True)


if __name__ == "__main__":
    import config_1tank as cfg
    from scheduler_1tank import build_1tank_schedule

    weekly_df, generations, cohorts, meta = build_1tank_schedule(cfg)
    ledger = build_resource_ledger(cfg, cohorts, generations)

    print("=== Ressursregnskap - forste 3 rader ===")
    print(ledger.head(3).to_string(index=False))

    print("\n=== Sum per kohort ===")
    print(summarize_by_cohort(ledger, cfg).to_string(index=False))

    print("\n=== Sum per ar ===")
    print(summarize_by_year(ledger, cfg).to_string(index=False))

    print("\n=== Timeverk - sum per kohort (separat oversikt) ===")
    labor = build_labor_hours(cfg, cohorts, generations)
    print(labor.groupby(labor["kohort_id"].str.strip("()"))["timer_totalt"].sum().to_string())
