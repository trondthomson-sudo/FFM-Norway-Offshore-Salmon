"""
config_1tank.py - postsmolt, ÉN TANK, manuelt tidsstyrt, 4x/år default
------------------------------------------------------------------------------
Startpunktet i den nye, større modellen: én Hexacage-tank, 4 kohorter/år,
solgt direkte som postsmolt (ingen split til vekstkar). Tenkt som byggekloss
#1 mot en modell som etter hvert skal håndtere 36 kohorter og flere ganger
så mange salgsbatcher, hver med egen PnL/kontantstrøm/balanse - se
resource_ledger.py for ressursregnskapet som bygges oppå denne.

Samme vekstmotor (Skretting SGR/FCR + temperaturprofil + RGI) som resten av
Hexacage-familien.
"""

# ----------------------------------------------------------------------
# 1. TANK / LOKALITET
# ----------------------------------------------------------------------
TANK_VOLUME_M3 = 83_333                # BEKREFTET av bruker: 6 vekstkar à 83 333 m3 = ca. 500 000 m3, Aqualoop Big Dipper

# ----------------------------------------------------------------------
# 2. MANUELL OPPSKRIFT-ROTASJON (gjentas automatisk: 1,2,3,4,1,2,3,4,...)
# ----------------------------------------------------------------------
START_WEIGHT_KG = 0.750               # postsmolt kjøpt inn FERDIG fra landbasert anlegg (BEKREFTET av bruker),
                                       # satt rett i vekstkaret - se BATCH_START_WEIGHT_KG for å sette ULIK
                                       # vekt per oppskrift; denne brukes som fallback for oppskrifter som
                                       # ikke har egen vekt satt.

N_BATCHES_IN_ROTATION = 1            # 1 oppskrift i rotasjon som default (denne filen = ÉN av seks vekstkar)

# Ett tall per oppskrift (indeks 0 = batch 1, osv.) - juster fritt i
# sidepanelet eller her, disse er utgangspunkt, ikke fasit.
BATCH_START_WEIGHT_KG = [START_WEIGHT_KG] * 6  # smoltvekt PER OPPSKRIFT (= per tank) - kan variere (f.eks. to kohorter
                                            # i året med ulik innsettvekt) - smoltprisen (formelbasert,
                                            # se SMOLT_PRICE_*) følger automatisk DENNE oppskriftens vekt.
BATCH_SMOLT_COUNTS = [850_000] * 6   # ÉN oppskrift PER TANK (tank 1..6). BEKREFTET av bruker: 850 000 stk
                                      # à 750 g (ca. 93 % av 500 000 m3 på topp - margin mot 25 kg/m3) - juster per tank/sesong
BATCH_GROWTH_WEEKS = [50] * 6  # BEKREFTET av bruker: 50 uker i tank - siste av 8 batcher selges i uke 50.
                               # (750g -> ~4,6 kg ved 50 uker; ~5 kg ville krevd ~54 uker.)
BATCH_CLEANING_WEEKS = [1] * 6  # BEKREFTET av bruker (1 uke, ikke Hexacage sine 2) - vasketid mellom kohorter.
                               # NB: 50 + 1 = 51 uker per syklus - innsettuken glir 1 uke tidligere per år.

# Antall uker en kohort SELGES over, per oppskrift (default 1 = alt levert i
# én uke, som før). Ved f.eks. 4: de FIRE SISTE vekstukene blir egne
# batcher, solgt med avtakende andel av gjenværende bestand (1/4, 1/3, 1/2,
# 1/1) - gir jevnstore batcher (likt antall fisk per batch). Se
# scheduler_1tank.py sin _split_i_batcher().
BATCH_SALES_WINDOW_WEEKS = [8] * 6

# ----------------------------------------------------------------------
# 3. TETTHET (varselnivå - modellen styrer ikke automatisk mot dette)
# ----------------------------------------------------------------------
MAX_DENSITY_KG_M3 = 25.0               # BEKREFTET av bruker - lavere enn Hexacage sine 60

# ----------------------------------------------------------------------
# 4. DØDELIGHET
# ----------------------------------------------------------------------
ANNUAL_MORTALITY_PCT = 5.8377          # eksakt ekvivalent til 0,5 %/MÅNED (1-(1-0,005)^12), BEKREFTET av bruker

# ----------------------------------------------------------------------
# 5. VEKSTYTELSE / TEMPERATUR
# ----------------------------------------------------------------------
RGI_PCT = 100.0
TEMPERATURE_PROFILES = {
    "Aqualoop Big Dipper - lokasjon": [
        5.00, 5.50, 6.00, 7.50, 9.00, 10.00, 10.50, 9.00, 8.50, 7.50, 7.00, 6.50,
    ],
}
DEFAULT_TEMPERATURE_PROFILE = "Aqualoop Big Dipper - lokasjon"
MONTHLY_TEMPERATURES_C = TEMPERATURE_PROFILES[DEFAULT_TEMPERATURE_PROFILE]

# ----------------------------------------------------------------------
# 6. KALENDER
# ----------------------------------------------------------------------
START_ISO_YEAR = 2026
START_ISO_WEEK = 1                   # tank 1 sitt første innsett (BEKREFTET av bruker: uke 1, deretter +8 per tank)
N_YEARS_TO_RUN = 12                  # 2026 -> 2037

# ----------------------------------------------------------------------
# 6b. MULTI-TANK (Big Dipper, Slaktefisk) - N PARALLELLE tanker med SAMME
#     oppskrift, innsett forskjøvet TANK_STAGGER_WEEKS uker per tank:
#     tank 1 i uke 1, tank 2 i uke 9, tank 3 i uke 17, ... (1 + 8 + 8 + ...).
#     Se scheduler_multitank.py. Gjelder KUN produkttype Slaktefisk -
#     Postsmolt 2x kjører fortsatt som ÉN tank med oppskrifter i rotasjon.
# ----------------------------------------------------------------------
N_TANKS = 6                          # BEKREFTET av bruker: 6 vekstkar à 83 333 m3 (ca. 500 000 m3 totalt)
TANK_STAGGER_WEEKS = 8               # brukes kun ved innsettmønster "Fast antall uker"

# To ferdige oppsett for å illustrere forskjellen for ABD-ingeniørene
# (BEKREFTET av bruker). Appen bytter automatisk når "Kakestykker" endres:
#  - SKYVESKOTT: 6 fleksible kakestykker à 83 333 m3 (500 000 m3), 850 000
#    fisk per kohort - tetthetstaket gjelder anlegget samlet.
#  - FASTE SKOTT: 8 låste kakestykker à 62 500 m3 (500 000 m3). Smoltantallet
#    per tank er KALIBRERT slik at hver kohort holder seg under 25 kg/m3 i
#    sitt eget skott hele veien (topp 24,7-24,8 kg/m3, 1 % margin) - ulikt
#    per tank fordi vekstsesongen er ulik.
SKYVESKOTT_DEFAULTS = {"n_tanks": 6, "tank_volume_m3": 83_333, "smolt": [850_000] * 6}
FASTE_SKOTT_DEFAULTS = {"n_tanks": 8, "tank_volume_m3": 62_500,
                        "smolt": [418_000, 402_000, 399_000, 399_000, 406_000, 431_000, 452_000, 432_000]}
# Default innsettmønster i appen: "Annenhver måned" - første mandag i jan,
# mar, mai, jul, sep, nov (BEKREFTET av bruker, gir rene månedsgrenser i
# kakediagrammene). Appen regner ut TANK_START_WEEK_OFFSETS fra startåret;
# None her = bruk fast stagger når filen kjøres utenom appen.
TANK_START_WEEK_OFFSETS = None

# ----------------------------------------------------------------------
# 7. RESSURSREGNSKAP - ressursdefinisjoner
#    Matcher en standard COGS-struktur for matfiskproduksjon (samme
#    linjenummerering som i "Financial Farming Model"):
#      0/1  Kjøpt smolt, fôrforbruk         - egne beregningsmåter (se under)
#      2/3  MGO (energi), oksygen            - liter/kg og kg O2/kg WFE
#      5-12 Resten                           - foreløpig ÉN flat enhet =
#                                              1 x kg WFE produsert, som gir
#                                              NOK/kg WFE en-til-en når
#                                              prisen fylles inn senere
#    "kg WFE produsert" = netto biomasseendring den uken (biomasse denne
#    uken minus forrige uke, der uke 0 sin "forrige" er innsatt smoltbiomasse)
#    - samme "netto tilvekst"-prinsipp som massebalanse-formlene ellers i
#    Hexacage-prosjektet.
# ----------------------------------------------------------------------
RESOURCES = [
    # id                          navn                                    enhet      kilde     faktor_per_kg_wfe
    {"id": "smolt",                "navn": "0. Kjøpt smolt",                "enhet": "stk",     "kilde": "smolt"},
    {"id": "for",                  "navn": "1. Fôrforbruk",                 "enhet": "kg",      "kilde": "feed"},
    # 2. MGO (Marine Gas Oil) - erstatter elektrisitet (BEKREFTET av bruker):
    #    energibehov 1 MWh per tonn WFE produsert (= 1 kWh/kg WFE), omregnet til
    #    liter diesel via MGO_*-forutsetningene under. faktor_per_kg_wfe her =
    #    liter MGO per kg WFE = MGO_KWH_PER_KG_WFE / (MGO_KWH_PER_LITER x MGO_VIRKNINGSGRAD).
    {"id": "energi",               "navn": "2. MGO (Marine Gas Oil)",       "enhet": "liter",   "kilde": "wfe", "faktor_per_kg_wfe": 0.245},
    {"id": "oksygen",              "navn": "3. Oksygen",                    "enhet": "kg O2",   "kilde": "wfe", "faktor_per_kg_wfe": 0.125},
    {"id": "annet_direkte_material", "navn": "5. Annet direkte materialforbruk", "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "annet_direkte_lonn",   "navn": "6. Andre direkte lønnskostnader", "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "slakt",                "navn": "7. Slaktevirksomhet",           "enhet": "kg WFE",  "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "distribusjon",         "navn": "8. Distribusjonsvirksomhet",    "enhet": "kg WFE",  "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "indirekte_material",   "navn": "10. Indirekte materialer",      "enhet": "kg WFE",  "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "indirekte_lonn",       "navn": "11. Indirekte lønn",            "enhet": "kg WFE",  "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "andre_produksjon",     "navn": "12. Forsikring av biomasse",     "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
]

# ---- MGO-forutsetninger (linje 2) ----
MGO_KWH_PER_KG_WFE = 1.0        # BEKREFTET av bruker: 1 MWh per tonn WFE produsert (= 1 kWh/kg)
MGO_KWH_PER_LITER = 10.2        # energiinnhold MGO: ca. 42,7 MJ/kg x 0,86 kg/l = 36,7 MJ/l = 10,2 kWh/l (ANTAKELSE)
MGO_VIRKNINGSGRAD = 0.40        # dieselgenerator, drivstoff -> elektrisk energi (ANTAKELSE, typisk 38-42 %)
# -> liter per kWh levert = 1 / (10,2 x 0,40) = 0,245 l/kWh -> 0,245 l/kg WFE, 245 l/tonn WFE

# WFE-faktor: 1,0 = standard bransjedefinisjon, WFE = levendevekt (biomasse).
WFE_FAKTOR = 1.0

# ----------------------------------------------------------------------
# 8. RESSURSREGNSKAP - priser (NOK per enhet). Sett til None så lenge dere
#    ikke har faktiske tall - da vises kun MENGDE (stk/kg/kWh osv.), ikke
#    kr. Fyll inn etter hvert - for de 7 "kg WFE"-linjene (5,6,7,8,10,11,12)
#    er dette ren NOK/kg WFE, siden faktor_per_kg_wfe = 1.0 for alle disse.
# ----------------------------------------------------------------------
RESOURCE_PRICES_NOK = {
    "smolt": None,                    # kr/stk - se SMOLT_PRICE_*-formelen under (brukes i appen som default)
    "for": 17.0,                      # kr/kg fôr
    "energi": 11.0,                    # kr/liter MGO (ANTAKELSE - sjekk mot faktisk bunkerspris levert offshore)
    "oksygen": 5.0,                    # kr/kg O2
    "annet_direkte_material": 1.0,    # kr/kg WFE
    "annet_direkte_lonn": 1.0,        # kr/kg WFE
    "slakt": 2.0,                     # kr/kg WFE
    "distribusjon": 0.0,               # kr/kg WFE
    "indirekte_material": 0.50,       # kr/kg WFE
    "indirekte_lonn": 0.50,           # kr/kg WFE
    "andre_produksjon": None,          # kr/kg WFE - beregnes normalt fra formelen under, se BIOMASSEFORSIKRING_DEFAULTS
}

# Smoltpris følger en formel i stedet for en fast sats: en fastdel + en sats
# per gram smoltvekt (fisken koster mer å kjøpe jo større den er ved
# innsett). Eksempel: 50 g smolt -> 10 + 50 x 0,10 = NOK 15/stk.
# Brukes som DEFAULT i sidepanelet (streamlit_app_1tank.py) - kan overstyres
# med en fast kr/stk-pris der i stedet, se avkrysningsboksen "Bruk formel".
SMOLT_PRICE_BASE_KR = 10.0          # kr, fast del per fisk
SMOLT_PRICE_PER_GRAM_KR = 0.10      # kr per gram smoltvekt (= 10 øre/gram)

# ----------------------------------------------------------------------
# 7b. SMOLTPRIS-MODELL: FISKEVERDITABELL (alternativ til formelen over)
#     Kr/kg "fiskeverdi" ved en gitt vekt, brukt til å prise INNKJØPT
#     SMOLT (0. Kjøpt smolt) - IKKE den ferdig leverte/slaktede fisken,
#     som fortsatt prises med den ordinære salgsprisen (kr/kg HOG/WFE,
#     se SALES_PRICE_KR_PER_KG i sidepanelet - "for slaktefisk legger du
#     utsalgsprisen til grunn", som brukeren selv formulerte det).
#
#     REVIDERT (3. versjon - "avtagende margin, glattet mot 85"):
#     bygget nedenfra fra variabel produksjonskostnad (smolt + fôr +
#     ressurser 0-12, se samtalen), med en AVTAGENDE margin-multiplikator
#     - 2,0x variabel kostnad tidlig (100 g), glidende ned mot 1,5x ved
#     700 g - og deretter et glattet gulv på 85 kr/kg fra ca. 500-700 g og
#     oppover (i stedet for et skarpt "flatt fra og med"-hopp). Glattet med
#     et vektet glidende gjennomsnitt (3 runder) over overgangssonen for å
#     fjerne knekken. 60 g er unntatt margin-regelen og beholder det
#     opprinnelige 266,7 kr/kg-ankeret (samme logikk som brukeren selv la
#     til grunn: den som selger 60 g-smolt for 16 kr/stk har allerede en
#     innebygd margin der).
#
#     Kr/stk (pris per fisk) er VERIFISERT MONOTONT STIGENDE hele veien fra
#     60 g til 1000 g (16,0 -> 85,0 kr) - ingen topp-og-fall, i motsetning
#     til både den opprinnelige tabellen OG et par mellomliggende forsøk
#     underveis (se samtalen for utregningene som ble forkastet).
#
#     Vekter MELLOM tabellpunktene interpoleres LINEÆRT (se
#     interpoler_fiskeverdi_kr_per_kg() i resource_ledger.py). Vekt UNDER
#     60 g bruker 60 g sin sats (266,7 kr/kg); vekt OVER 1000 g bruker
#     1000 g sin sats (85,0 kr/kg).
# ----------------------------------------------------------------------
SMOLT_VERDITABELL_KR_PER_KG = [
    (60, 266.7), (100, 245.9), (150, 219.0), (200, 186.9), (250, 156.1),
    (300, 132.0), (350, 114.3), (400, 101.1), (500, 91.8), (600, 86.9),
    (700, 85.2), (800, 85.0), (900, 85.0), (1000, 85.0),
]

# Hvilken av de to modellene som brukes, PER OPPSKRIFT (kan overstyres av
# produkttype-presetene i sidepanelet - alle fire (Slaktefisk, Postsmolt
# 2x/3x/4x) bruker nå tabellen som default). "Formel" eller "Tabell".
SMOLT_PRIS_MODUS = "Tabell"

# ----------------------------------------------------------------------
# 8a2. SESONGVARIERT SALGSPRIS - valgfri, ukentlig indeksering av
#      salgsprisen rundt et fast årssnitt. Default AV (USE_SEASONAL_
#      PRICE_INDEX = False) - salgsprisen er da FLAT hele året, som før
#      (uendret oppførsel). Slås PÅ i sidepanelet under "Salg".
#
#      Prinsipp: hvis salgsprisen (evt. allerede årlig eskalert, se
#      ESCALATION_RATES_BY_YEAR sin "inntekt"-linje) er f.eks. 100 kr/kg,
#      gir indeks 1,08 i uke 15 en FAKTISK pris den uken på 108 kr/kg,
#      mens indeks 0,80 i uke 39 gir 80 kr/kg - årssnittet blir likevel
#      100 kr/kg SÅ LENGE indeksens eget snitt over de 52 ukene er 1,00
#      (se normaliseringen under - garanterer nøyaktig snitt = 1,00
#      uansett hvor godt de rå tallene under er avlest/anslått).
#
#      SEASONAL_PRICE_INDEX_BY_WEEK_RAW er de FAKTISKE ukentlige
#      indekstallene oppgitt av bruker (basert på "AVERAGE"-linjen i en
#      ukentlig NOS spot-pris-graf for laks, 2011-2025) - IKKE lenger en
#      avlest tilnærming. Fortsatt fritt redigerbar i sidepanelet (samme
#      prinsipp som ESCALATION_RATES_BY_YEAR) hvis tallene oppdateres.
# ----------------------------------------------------------------------
USE_SEASONAL_PRICE_INDEX = False

SEASONAL_PRICE_INDEX_BY_WEEK_RAW = {
    1: 1.00, 2: 1.00, 3: 0.98, 4: 0.95, 5: 0.94, 6: 0.96, 7: 1.00, 8: 1.04,
    9: 1.06, 10: 1.05, 11: 1.05, 12: 1.05, 13: 1.05, 14: 1.08, 15: 1.08,
    16: 1.10, 17: 1.08, 18: 1.08, 19: 1.07, 20: 1.09, 21: 1.05, 22: 1.00,
    23: 1.00, 24: 1.02, 25: 0.98, 26: 0.94, 27: 0.97, 28: 0.97, 29: 0.94,
    30: 0.89, 31: 0.86, 32: 0.87, 33: 0.85, 34: 0.81, 35: 0.81, 36: 0.82,
    37: 0.80, 38: 0.79, 39: 0.80, 40: 0.82, 41: 0.84, 42: 0.85, 43: 0.84,
    44: 0.85, 45: 0.84, 46: 0.87, 47: 0.91, 48: 0.93, 49: 0.97, 50: 1.02,
    51: 1.00, 52: 1.08,
}
_seasonal_snitt = sum(SEASONAL_PRICE_INDEX_BY_WEEK_RAW.values()) / len(SEASONAL_PRICE_INDEX_BY_WEEK_RAW)
# Normalisert versjon - GARANTERT snitt = 1,0000 eksakt, uavhengig av
# presisjonen i avlesningen over.
SEASONAL_PRICE_INDEX_BY_WEEK = {
    uke: round(verdi / _seasonal_snitt, 4) for uke, verdi in SEASONAL_PRICE_INDEX_BY_WEEK_RAW.items()
}

# ----------------------------------------------------------------------
# 8b. PRODUKTTYPE - styrer to ting:
#     1) Postsmolt har VERKEN slaktekostnad (7) ELLER distribusjonskostnad
#        (8) - disse nullstilles automatisk når PRODUKTTYPE = "Postsmolt"
#        (uansett hva faktor/pris er satt til i sidepanelet).
#     2) Slaktefisk selges som HOG (hodekappet vekt), ikke WFE (levendevekt)
#        - HOG_FAKTOR regner om levert WFE til faktisk SOLGT vekt, som
#          igjen er grunnlaget for inntektsberegningen (kr/kg-prisen
#          forutsettes da å være kr/kg HOG, ikke kr/kg WFE).
#     Postsmolt selges derimot som WFE direkte (HOG_FAKTOR = 1,0, brukes
#     ikke).
# ----------------------------------------------------------------------
PRODUKTTYPE = "Slaktefisk"    # "Postsmolt" eller "Slaktefisk"
HOG_FAKTOR = 0.825            # HOG-vekt som andel av WFE - kun brukt når PRODUKTTYPE = "Slaktefisk"

# ----------------------------------------------------------------------
# 8c. FASTE KOSTNADER (13-15) - UAVHENGIGE av batch/kohort, relevante kun
#     for KONSOLIDERT kontantstrøm (hele anlegget, kalendertid), IKKE for
#     enkeltbatch-visningene (en batch kan jo ikke "eie" en andel av
#     anleggsleien). Satt som en fast sats PER UKE, påløper HVER
#     kalenderuke i simuleringsperioden, uavhengig av om en kohort er i
#     vekst, vask, eller tanken står "Ledig".
# ----------------------------------------------------------------------
FIXED_COSTS = [
    {"id": "leie_anlegg", "navn": "13. Leie av Big Dipper-anlegg"},
    {"id": "bronnbat", "navn": "14. Årlig leie av brønnbåter"},                 # NY (bekreftet av bruker)
    {"id": "teknisk_vedlikehold", "navn": "15. ADK (andre driftskostnader)"},   # omdøpt fra "Teknisk vedlikehold", dyttet fra 14 til 15
    {"id": "administrasjon", "navn": "16. Administrasjonskostnader"},           # dyttet fra 15 til 16
]

# NOK per uke, per linje. None = ikke satt ennå (telles som 0 i konsolidert
# kontantstrøm inntil dere har tall). "leie_anlegg" (13) beregnes normalt
# ut fra underlinjene i HEXACAGE_LEIE_DEFAULTS under, i stedet for å
# skrives inn direkte - se streamlit_app_1tank.py sin egen underseksjon.
FIXED_COST_KR_PER_UKE = {
    "leie_anlegg": None,
    "bronnbat": 200_000_000.0 / 52.0,             # 14. Brønnbåter: 200 MNOK/år (BEKREFTET av bruker)
    "teknisk_vedlikehold": 20_000_000.0 / 52.0,   # 15. ADK: 20 MNOK/år (BEKREFTET av bruker)
    "administrasjon": 20_000_000.0 / 52.0,        # 16. Administrasjon: 20 MNOK/år (BEKREFTET av bruker)
}

# ----------------------------------------------------------------------
# 8c2. "12. Forsikring av biomasse" - EGEN formel, ikke en flat kr/kg-sats:
#      forsikret verdi (kr/kg WFE) = andel_av_salgspris x salgspris
#      pris (kr/kg WFE) = forsikringssats x forsikret verdi
#      Eksempel: 50 % x 100 kr/kg = 50 kr/kg forsikret verdi,
#                3 % x 50 kr/kg = 1,50 kr/kg WFE i forsikringskostnad.
#      Faktoren i RESOURCES ("faktor_per_kg_wfe": 1.0) er uendret - det er
#      KUN prisen (kr/kg) som nå beregnes fra denne formelen i stedet for
#      å skrives inn direkte.
# ----------------------------------------------------------------------
BIOMASSEFORSIKRING_DEFAULTS = {
    "andel_av_salgspris_pct": 0.50,
    "forsikringssats_pct": 0.03,
}

# ----------------------------------------------------------------------
# 8d. UNDERLINJER for "13. Leie av Big Dipper-anlegg" - dette ER det
#     oppdretter skal betale Hexacage, satt sammen av seks komponenter.
#     Alle beløp er PER ÅR (kapitalleie og annuitetslånet regnes om til
#     årlig ekvivalent internt).
# ----------------------------------------------------------------------
HEXACAGE_LEIE_DEFAULTS = {
    # 1) Kapitalleie = CAPEX x sats. CAPEX = NOK 2,5 mrd. for HELE Big
    # Dipper-enheten (seks vekstkar, ca. 500 000 m3 totalt) - BEKREFTET
    # av bruker at dette ligger som EN FELLES fast kostnad for hele
    # anlegget, IKKE delt opp per tank. Med multi-tank-modellen
    # (N_TANKS = 6, scheduler_multitank.py) møter denne kapitalleien nå
    # HELE anleggets inntekt, slik den skal.
    "capex_nok": 2_500_000_000.0,
    "kapitalleie_pct": 0.12,           # BEKREFTET av bruker: 12 % (ikke Hexacage sine 14 %)

    # 2) Oppankring - 5-års annuitetslån (60 mnd, månedlig nedbetaling).
    #    BEKREFTET av bruker: 0 for Big Dipper - oppankringen ligger
    #    allerede inne i de 2,5 mrd CAPEX over. Strukturen beholdes (sett
    #    et beløp i sidepanelet hvis det skal skilles ut igjen).
    "oppankring_investering_nok": 0.0,
    "oppankring_nedbetaling_maneder": 60,
    "oppankring_rente_pct_ar": 0.12,   # nominell årsrente, delt på 12 for månedlig sats

    # 2b) Teknisk vedlikehold - fast NOK/år i startåret (eskaleres), del av
    #     leien (gjennomfakturert). BEKREFTET av bruker: 10 MNOK/år.
    "teknisk_vedlikehold_nok_per_ar": 10_000_000.0,

    # 3) Årlig rengjøring innvendig - fast NOK/år.
    "rengjoring_innvendig_nok_per_ar": 2_000_000.0,   # BEKREFTET av bruker (Big Dipper)

    # 4) Årlig rengjøring av krager - fast NOK/år.
    "rengjoring_krager_nok_per_ar": 1_000_000.0,      # BEKREFTET av bruker (Big Dipper)

    # 5) Desinfeksjon - NOK PER KOHORT/GENERASJON (IKKE en jevn ukentlig/
    #    årlig sats som resten av linjene - påløper kun i den uken en ny
    #    kohort settes inn, én gang per kohort).
    "desinfeksjon_nok_per_kohort": 100_000.0,        # BEKREFTET av bruker (Big Dipper)

    # 6) Lønn lokalitet - NOK/år PER PERSON, antall justerbart.
    "lonn_lokalitet_nok_per_ar": 1_600_000.0,   # BEKREFTET av bruker (Big Dipper)
    "lonn_lokalitet_antall": 10,

    # 7) Lønn land - NOK/år PER PERSON, antall justerbart.
    "lonn_land_nok_per_ar": 2_000_000.0,        # BEKREFTET av bruker (Big Dipper)
    "lonn_land_antall": 2,                      # BEKREFTET av bruker

    # Sosiale kostnader - prosent PÅ HVER AV lønn lokalitet og lønn land
    # (proporsjonalt fordelt, ikke lagt i én av linjene).
    "sosiale_kostnader_pct": 0.32,

    # 8) Forsikring anlegg og fartøy - fast NOK/år.
    "forsikring_pct": 0.0075,      # 0,75 % av CAPEX (samme CAPEX som post 1) = NOK 1 875 000 med default CAPEX

    # 9) ADK (andre driftskostnader) - fast NOK/år.
    "adk_nok_per_ar": 1_000_000.0,
}

# ----------------------------------------------------------------------
# 8e. UTLEIER SIN EGEN LØNNSOMHETSMODELL (Aqualoop/Big Dipper som eier av
#     anlegget) - ATSKILT fra oppdretters P&L/balanse ellers i modellen.
#     Bygger på "13. Leie av Big Dipper-anlegg" sine underlinjer (se over),
#     men fra UTLEIERS ståsted: leieinntekter minus driftskostnader gir
#     EBITDA (= kapitalleien, 13.1), og "13.2 Oppankring" splittes i en
#     rentedel (finansinntekt for utleier) og en avdragsdel (nedbetaling
#     av UTLEIERS utlån til oppdretter - se HEXACAGE_LEIE_DEFAULTS sin
#     forklaring: oppankringslånet er utleiers eget lån, ikke oppdretters).
#
#     Denne seksjonen (utleiers EGEN banklånsfinansiering av CAPEX,
#     skattesats, vedlikeholdsinvestering) er HELT NYE antakelser, IKKE
#     hentet fra noe annet sted i modellen og IKKE bekreftet mot faktiske
#     lånevilkår - kun et utgangspunkt for å ha noe fornuftig å justere
#     fra i sidepanelet.
# ----------------------------------------------------------------------
UTLEIER_DEFAULTS = {
    "ebitda_multipel": 5.0,                       # utleiers banklån = X ganger EBITDA (= kapitalleien)
    "swap_rente_pct": 0.04,                       # swap-rente (%/år)
    "kredittpaslag_pct": 0.035,                   # kredittpåslag over swap (%/år) - sammen: bankrente = swap + påslag
    "banklan_nedbetaling_ar": 12,                 # nedbetalingstid i år (annuitet)
    "skattesats_pct": 0.22,                       # norsk selskapsskattesats
    "vedlikeholdsinvestering_nok_forste_ar": 10_000_000.0,  # BEKREFTET av bruker: 10 MNOK første år (i NOK,
                                                            # ikke % av CAPEX), deretter inflasjonsjustert
    "vedlikeholdsinvestering_pct_capex": 0.004,   # (avledet: NOK / CAPEX = 0,4 % - brukes internt av utleiermodellen)
    "vedlikeholdsinvestering_indeksering_pct_ar": 0.02,  # deretter eskalert PER ÅR
    "avskrivningstid_ar": 40,                     # BEKREFTET av bruker: lineær avskrivning av ABD over 40 år (og vedlikeholdsinvesteringer)
    "refi_intervall_ar": 4,                       # refinansier banklånet hvert 4. år (0 = av) ...
    "refi_multipel": 5.0,                         # ... til X ganger NESTE års EBITDA - proveny til eier
    "terminal_ebitda_multipel": 10.0,             # IRR: terminalverdi = X ganger EBITDA året ETTER holdeperioden
    "holding_years": 10,                          # IRR: eiertid i år før exit/terminalverdi
}

# ----------------------------------------------------------------------
# 9. OUTPUT
# ----------------------------------------------------------------------
OUTPUT_DIR = "output"

# ----------------------------------------------------------------------
# 10. RUN AS SCRIPT - liten sjekk av at rotasjonen faktisk gir en gyldig,
#     kollisjonsfri (ingen overlapp) syklus, siden 1-tank-modellen (i
#     motsetning til 3-tank-varianten) ikke har noe eget vekstkar som kan
#     ta unna en forsinkelse - hvis growth+cleaning per oppskrift ikke
#     summerer fornuftig, glir bare hele rotasjonen ut i tid (ikke en feil,
#     men lurt å vite om for de som er vant til at 52 uker er målet).
# ----------------------------------------------------------------------
if __name__ == "__main__":
    _sum = sum(BATCH_GROWTH_WEEKS[i] + BATCH_CLEANING_WEEKS[i] for i in range(N_BATCHES_IN_ROTATION))
    print(f"Full rotasjon (tank): {_sum} uker (mal: 52 for en 'recurring' arsplan)")


# ----------------------------------------------------------------------
# 9. RESULTATREGNSKAP - under EBITDA (oppdretters egne poster)
#    Anlegget eies av utleier (13. Leie er opex) - så avskrivninger og
#    finanskostnader her gjelder KUN oppdretters EGNE eiendeler/lån
#    (default 0). Skatt: 22 % selskapsskatt med fremførbart underskudd.
#    NB: skatt/avskrivninger/finans vises FORELØPIG kun i Resultatregnskapet -
#    de er IKKE bokført i Konsolidert kontantstrøm eller Balanse ennå.
# ----------------------------------------------------------------------
# Reforhandling av TC ("ny kapitalleie-sats på riggen") underveis - inntil
# to tidspunkter. Ny 13.1 = dagens eskalerte 13.1 + andel x (EBITDA-krav fra
# nybyggparitet - dagens 13.1). BEKREFTET av bruker som default: 2031 og
# 2036, 50 % av gapet hver gang. ar = None -> ikke i bruk.
TC_REFORHANDLING_DEFAULTS = [
    {"ar": 2031, "andel": 0.50},
    {"ar": 2036, "andel": 0.50},
]

# Nybyggparitet - guide for ny TC (BEKREFTET-format av bruker): nybyggpris
# = CAPEX indeksert med byggeindeks fra 2026; EBITDA-krav = nybyggpris x
# EBITDA-yield; guide-TC = EBITDA-krav + årets driftskostnader i leien.
NYBYGGPARITET_DEFAULTS = {
    "byggeindeks_pct_ar": 0.04,     # nybyggpris øker 4 %/år
    "ebitda_yield_pct": 0.12,       # EBITDA-krav = 12 % av nybyggpris
}

RESULTAT_DEFAULTS = {
    "avskrivninger_nok_per_ar": 0.0,
    "finanskostnader_nok_per_ar": 0.0,
    "skattesats_pct": 0.22,
}


# ----------------------------------------------------------------------
# 10. NÅVERDI (DCF) - oppdretters kontantstrøm, totalkapitalmodellen (ubelånt)
#     BEKREFTET av bruker: rf 4 %, markedspremie 5 %, eiendelsbeta 0,80
#     -> r = 4 % + 0,80 x 5 % = 8,0 %. Sluttverdi 10x EBITDA (justerbar).
# ----------------------------------------------------------------------
DCF_DEFAULTS = {
    "rf_pct": 0.04,
    "mp_pct": 0.05,
    "beta": 0.80,
    "horisont_ar": 10,
    "ev_ebitda": 10.0,
}
