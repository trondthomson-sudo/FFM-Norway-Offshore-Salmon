"""
config_kvidul.py - Kvidul AS, Brennholmen: post-smolt til Aqualoop Big Dipper
------------------------------------------------------------------------------
Kvidul produserer post-smolt (750 g) på land (RAS, AquaMaof) og leverer til
Norway Offshore Salmon (NOS) sine Big Dipper-enheter (ABD). Denne filen er
Kvidul-modellens motstykke til config_1tank.py i FFM Big Dipper - samme
struktur og samme navn på feltene resource_ledger.py trenger, slik at
ledger/P&L/balanse fungerer uendret nedstrøms.

HOVEDFORSKJELLER fra Big Dipper-konfigen:
  1. Kohortene plasseres BAKOVER fra ABD sin innsettuke (= Kvidul sin
     leveringsuke), ikke fremover fra en tankrotasjon. Se
     scheduler_kvidul.py.
  2. Kapasitet sjekkes per TRINN (yngel / smolt / post-smolt / evt. fase 2)
     med et karpool (antall kar x m3) og et tetthetstak per trinn - ikke
     per fast tank. Fisken tilhører trinnet etter VEKT.
  3. Produkttype er alltid Postsmolt (WFE, ingen slakt/distribusjon).
  4. Kvidul EIER anlegget selv - ingen SFaaS/utleier-lag.

Kilder: Kvidul_20260218.pdf side 11 (anleggsdata fase 1), side 19 (CAPEX og
finansieringsplan), samtale 11.09.2026 (rogn, vaksine, 0,10 kr/g,
tetthetstak 50 kg/m3, 850 000 stk a 750 g per ABD-leveranse).
"""

# ----------------------------------------------------------------------
# 1. LEVERANSEPLAN - hva som skal UT (styrt av ABD-innsettene hos NOS)
# ----------------------------------------------------------------------
N_ABD = 1                              # antall Big Dipper-enheter Kvidul leverer til (1 eller 2).
                                       # BEKREFTET av bruker: én ABD i første omgang; nr. 2 krever fase 2-hallen.
LEVERANSER_PER_ABD_PER_AR = 6          # 6 innsett per ABD per år (jan/mar/mai/jul/sep/nov i Big Dipper-modellen)
ABD_INNSETT_MANEDER = {                # kalendermåneder for innsett per ABD - første MANDAG i måneden,
    1: [1, 3, 5, 7, 9, 11],            # samme regel som "Jevnt fordelt over året" i streamlit_app_1tank.py
    2: [2, 4, 6, 8, 10, 12],           # ABD 2 forskjøvet én måned - gir 12 jevne leveranser i året
}
LEVERT_ANTALL_PER_LEVERANSE = 850_000  # BEKREFTET av bruker (= smoltantall per kohort i Big Dipper-modellen)
LEVERT_VEKT_KG = 0.750                 # BEKREFTET av bruker: 750 g (Kvidul sitt eget design er 560 g)
INNSETT_ANTALL_MODUS = "auto"          # "auto": innsettantall ved 30 g regnes ut fra dødelighet slik at
                                       # LEVERT_ANTALL_PER_LEVERANSE faktisk leveres; "fast": bruk INNSETT_ANTALL_FAST
INNSETT_ANTALL_FAST = 875_000
INNSETT_ANTALL_AVRUNDING = 1_000       # rund innsettantall opp til nærmeste tusen

LEVERANSE_START_AR = 2028              # første ABD-innsett Kvidul leverer til. Kvidul-deck s. 3: "Salg av smolt
                                       # vil være mulig første halvdel av 2028". Første Kvidul-innsett ligger
                                       # vekstuker FØR dette (regnes ut av scheduleren).
N_YEARS_TO_RUN = 10                    # antall leveranseår som simuleres
SALGSVINDU_UKER = 1                    # hele kohorten leveres til ABD i ÉN uke (brønnbåt)
VASKEUKER_ETTER_LEVERING = 1           # karene vaskes/desinfiseres etter at kohorten er flyttet ut

# ----------------------------------------------------------------------
# 2. FISKEN INN - 30 g yngel fra eget klekkeri/startfôring
# ----------------------------------------------------------------------
START_WEIGHT_KG = 0.030                # BEKREFTET av bruker: modellen starter ved 30 g
BATCH_START_WEIGHT_KG = [START_WEIGHT_KG]

# Variabel selvkost inn til 30 g (BEKREFTET av bruker): rogn 3,75 + vaksine
# 6,25 = 10,00 kr fast del, pluss 0,10 kr per gram -> 13,00 kr/stk ved 30 g.
# Vaksinen settes fysisk ved 50-70 g, men kostnadsføres i innsettuken sammen
# med rognen (forenkling - det er samme kohort og samme uke +/- 6 uker).
# De 3 kr/stk (0,10 x 30) er et ANSLAG på klekkeri-/startfôringskostnad
# (fôr, strøm, lønn) - byttes ut når Kvidul har egne tall.
ROGN_KR_PER_STK = 3.75
VAKSINE_KR_PER_STK = 6.25
SMOLT_PRICE_BASE_KR = ROGN_KR_PER_STK + VAKSINE_KR_PER_STK   # 10,00
SMOLT_PRICE_PER_GRAM_KR = 0.10
SMOLT_PRIS_MODUS = "Formel"            # Kvidul lager yngelen selv -> selvkost-formel, IKKE fiskeverditabellen

# ----------------------------------------------------------------------
# 3. TRINN (karpooler) - fase 1 Brennholmen, Kvidul-deck s. 11
#    Fisken tilhører trinnet etter vekt: vekt_fra_g <= vekt < vekt_til_g.
#    Siste aktive trinn er åpent oppover (tar fisken helt til levering).
#    Tetthetstak: BEKREFTET av bruker 50 kg/m3 (post-smolt). Yngel/smolt
#    er ANTAKELSER - juster når AquaMaof-design foreligger.
#    NB: tegningen s. 11 sier 8 smoltkar, tabellen sier 10 - bruker 8
#    (BEKREFTET av bruker).
# ----------------------------------------------------------------------
TRINN = [
    {"id": "yngel",     "navn": "Yngel (ferskvann)",      "vekt_fra_g": 0,    "vekt_til_g": 69,
     "antall_kar": 14, "kar_volum_m3": 191, "tetthetstak_kg_m3": 40.0, "aktiv": True},
    {"id": "smolt",     "navn": "Smolt (saltvann)",       "vekt_fra_g": 69,   "vekt_til_g": 157,
     "antall_kar": 8,  "kar_volum_m3": 883, "tetthetstak_kg_m3": 50.0, "aktiv": True},
    {"id": "postsmolt", "navn": "Post-smolt (fase 1)",    "vekt_fra_g": 157,  "vekt_til_g": None,
     "antall_kar": 24, "kar_volum_m3": 883, "tetthetstak_kg_m3": 50.0, "aktiv": True},
    # Fase 2 storsmolt-hall (30 x 3 165 m3 = 94 950 m3). Slås på for ABD nr. 2:
    # da tar fase 1-hallen fisken til FASE2_OVERGANG_G og fase 2 resten.
    {"id": "fase2",     "navn": "Storsmolt (fase 2)",     "vekt_fra_g": None, "vekt_til_g": None,
     "antall_kar": 30, "kar_volum_m3": 3_165, "tetthetstak_kg_m3": 50.0, "aktiv": False},
]
FASE2_OVERGANG_G = 400                 # når fase 2 er aktiv: fisk >= dette flyttes fra fase 1 post-smolt til fase 2
KAR_DELES_IKKE_MELLOM_KOHORTER = True  # biosikkerhet: et kar rommer kun én kohort -> karbehov rundes OPP per kohort

# Kompatibilitet med 1-tank-motoren/ledgeren (brukes ikke som tak i Kvidul):
TANK_VOLUME_M3 = 883
MAX_DENSITY_KG_M3 = 50.0               # BEKREFTET av bruker

# ----------------------------------------------------------------------
# 4. DØDELIGHET / VEKST / TEMPERATUR (RAS - styrt, konstant)
# ----------------------------------------------------------------------
ANNUAL_MORTALITY_PCT = 5.8377          # 0,5 %/mnd (samme som Big Dipper) - ANTAKELSE for RAS, ofte lavere
RGI_PCT = 100.0
TEMPERATURE_PROFILES = {
    "Kvidul RAS 12 °C (konstant)": [12.0] * 12,     # Kvidul-deck s. 11: 12 °C smolt/post-smolt
    "Kvidul RAS 10 °C (konstant)": [10.0] * 12,
    "Kvidul RAS 14 °C (konstant)": [14.0] * 12,
}
DEFAULT_TEMPERATURE_PROFILE = "Kvidul RAS 12 °C (konstant)"
MONTHLY_TEMPERATURES_C = TEMPERATURE_PROFILES[DEFAULT_TEMPERATURE_PROFILE]

# ----------------------------------------------------------------------
# 5. KALENDER - settes av scheduleren ut fra leveranseplanen:
#    uke 0 = første Kvidul-innsett (= første ABD-innsett minus vekstuker).
#    Verdiene her er kun fallback når filen kjøres utenom appen.
# ----------------------------------------------------------------------
START_ISO_YEAR = 2027
START_ISO_WEEK = 27

# ----------------------------------------------------------------------
# 6. RESSURSREGNSKAP - RAS-drivere. Samme id-er som Big Dipper der det er
#    mulig, slik at resource_ledger.py og appens kolonneoppsett kan gjenbrukes.
#    "kg WFE" = bruttovekst den uken (samme prinsipp som Big Dipper).
# ----------------------------------------------------------------------
RESOURCES = [
    {"id": "smolt",                  "navn": "0. Rogn, vaksine og yngel (30 g)", "enhet": "stk",    "kilde": "smolt"},
    {"id": "for",                    "navn": "1. Fôrforbruk",                    "enhet": "kg",     "kilde": "feed"},
    # 2. Strøm - RAS-anlegg: pumper, oksygenering, biofilter, temperering.
    #    ANTAKELSE: 6 kWh per kg WFE produsert (Kvidul-deck s. 16: 6 MWp for en
    #    10 mill.-smoltmodul; typisk RAS-litteratur 4-8 kWh/kg). Kalibreres.
    {"id": "energi",                 "navn": "2. Strøm (RAS)",                   "enhet": "kWh",    "kilde": "wfe", "faktor_per_kg_wfe": 6.0},
    # 3. Oksygen - RAS bruker mer enn åpen sjø. ANTAKELSE 0,5 kg O2/kg WFE.
    {"id": "oksygen",                "navn": "3. Oksygen",                       "enhet": "kg O2",  "kilde": "wfe", "faktor_per_kg_wfe": 0.5},
    {"id": "vann",                   "navn": "4. Vann, salt og kjemikalier",     "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "annet_direkte_material", "navn": "5. Annet direkte materialforbruk", "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "annet_direkte_lonn",     "navn": "6. Andre direkte lønnskostnader",  "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    # 7/8 beholdes med faktor 0 av hensyn til appens Postsmolt-logikk (nullstilles der uansett)
    {"id": "slakt",                  "navn": "7. Slaktevirksomhet",              "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 0.0},
    {"id": "distribusjon",           "navn": "8. Distribusjon (brønnbåt til ABD)", "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 0.0},
    {"id": "indirekte_material",     "navn": "10. Indirekte materialer",         "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "indirekte_lonn",         "navn": "11. Indirekte lønn",               "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "andre_produksjon",       "navn": "12. Forsikring av biomasse",       "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
]
WFE_FAKTOR = 1.0

RESOURCE_PRICES_NOK = {
    "smolt": None,                    # formel (SMOLT_PRICE_*): 13,00 kr/stk ved 30 g
    "for": 17.0,                      # kr/kg fôr (som Big Dipper)
    "energi": 1.0,                    # kr/kWh - ANTAKELSE (egen gassturbin i fase 1, nett fra 2030). Oppgis av bruker.
    "oksygen": 3.0,                   # kr/kg O2 - ANTAKELSE (landbasert, tank-levert)
    "vann": 0.5,                      # kr/kg WFE - ANTAKELSE
    "annet_direkte_material": 1.0,
    "annet_direkte_lonn": 2.0,        # RAS er mer bemannet per kg enn en ABD - ANTAKELSE
    "slakt": 0.0,
    "distribusjon": 0.0,
    "indirekte_material": 0.5,
    "indirekte_lonn": 0.5,
    "andre_produksjon": None,          # formel, se BIOMASSEFORSIKRING_DEFAULTS
}
BIOMASSEFORSIKRING_DEFAULTS = {"andel_av_salgspris_pct": 0.50, "forsikringssats_pct": 0.03}

# ----------------------------------------------------------------------
# 7. SALG - post-smolt selges som WFE til NOS. Prisen følger
#    fiskeverditabellen (samme tabell som NOS bruker for "0. Kjøpt smolt"),
#    slik at inntekt hos Kvidul = kostnad hos NOS, krone for krone.
#    750 g -> 85,2 kr/kg -> ca. 64 kr/stk.
# ----------------------------------------------------------------------
PRODUKTTYPE = "Postsmolt"
HOG_FAKTOR = 1.0
SALGSPRIS_MODUS = "Følger fiskeverditabellen"
SMOLT_VERDITABELL_KR_PER_KG = [
    (60, 266.7), (100, 245.9), (150, 219.0), (200, 186.9), (250, 156.1),
    (300, 132.0), (350, 114.3), (400, 101.1), (500, 91.8), (600, 86.9),
    (700, 85.2), (800, 85.0), (900, 85.0), (1000, 85.0),
]
USE_SEASONAL_PRICE_INDEX = False

# ----------------------------------------------------------------------
# 8. ANLEGGSKOSTNADER (13.x) OG FASTE KOSTNADER (14-16) - Kvidul EIER anlegget.
#    Appen gjenbruker Big Dipper sin "13. Leie"-struktur (samme felt/nøkler),
#    men med KAPITALLEIE = 0 - linjene 13.3-13.10 er da Kvidul sine EGNE
#    driftskostnader for anlegget (vedlikehold, rengjøring, desinfeksjon,
#    lønn, forsikring, ADK). Alle beløp er ANTAKELSER inntil Kvidul gir tall.
# ----------------------------------------------------------------------
HEXACAGE_LEIE_DEFAULTS = {
    "capex_nok": 1_472_788_000.0,      # CAPEX fase 1, nøytral kalkyle (Kvidul-deck s. 19)
    "kapitalleie_pct": 0.0,            # ingen leie - eget anlegg
    "oppankring_investering_nok": 0.0,
    "oppankring_nedbetaling_maneder": 60,
    "oppankring_rente_pct_ar": 0.0,
    "teknisk_vedlikehold_nok_per_ar": 15_000_000.0,   # ca. 1 % av CAPEX - ANTAKELSE
    "rengjoring_innvendig_nok_per_ar": 2_000_000.0,   # kar/rør/biofilter - ANTAKELSE
    "rengjoring_krager_nok_per_ar": 0.0,              # ikke relevant på land
    "desinfeksjon_nok_per_kohort": 100_000.0,         # per kohort ved innsett - ANTAKELSE
    "lonn_lokalitet_nok_per_ar": 1_200_000.0,         # driftsoperatører, per årsverk - ANTAKELSE
    "lonn_lokalitet_antall": 25,                      # RAS 10 mill. smolt-modul - ANTAKELSE
    "lonn_land_nok_per_ar": 2_000_000.0,              # ledelse/biologi/økonomi - ANTAKELSE
    "lonn_land_antall": 3,
    "sosiale_kostnader_pct": 0.32,
    "forsikring_pct": 0.0075,                         # 0,75 % av CAPEX (samme sats som Big Dipper)
    "adk_nok_per_ar": 5_000_000.0,                    # ANTAKELSE
}
FIXED_COSTS = [
    {"id": "leie_anlegg", "navn": "13. Anleggskostnader (eget anlegg)"},
    {"id": "bronnbat", "navn": "14. Brønnbåt / transport til ABD"},
    {"id": "teknisk_vedlikehold", "navn": "15. ADK (andre driftskostnader)"},
    {"id": "administrasjon", "navn": "16. Administrasjonskostnader"},
]
FIXED_COST_KR_PER_UKE = {
    "leie_anlegg": None,
    "bronnbat": 0.0,                        # ANTAKELSE: NOS henter fisken (ligger i NOS sine 200 MNOK brønnbåt)
    "teknisk_vedlikehold": 5_000_000.0 / 52.0,
    "administrasjon": 10_000_000.0 / 52.0,  # ANTAKELSE
}

# ----------------------------------------------------------------------
# 9. EIER - Kvidul som eget selskap (Kvidul-deck s. 19, finansieringsplan fase 1)
#    Appen bruker UTLEIER_DEFAULTS-nøklene i "Konsolidert"-maskineriet; for
#    Kvidul settes banklånet DIREKTE (BANKLAN_NOK), ikke som x EBITDA.
# ----------------------------------------------------------------------
BANKLAN_NOK = 739_000_000.0               # lån 739 / EK 733 MNOK (s. 19)
UTLEIER_DEFAULTS = {
    "ebitda_multipel": 0.0,
    "swap_rente_pct": 0.04,
    "kredittpaslag_pct": 0.035,
    "banklan_nedbetaling_ar": 12,
    "skattesats_pct": 0.22,
    "vedlikeholdsinvestering_nok_forste_ar": 10_000_000.0,
    "vedlikeholdsinvestering_pct_capex": 0.0068,
    "vedlikeholdsinvestering_indeksering_pct_ar": 0.02,
    "avskrivningstid_ar": 25,             # landanlegg/RAS - ANTAKELSE (ABD: 40)
    "refi_intervall_ar": 0,
    "refi_multipel": 0.0,
    "terminal_ebitda_multipel": 8.0,
    "holding_years": 10,
}
RESULTAT_DEFAULTS = {"avskrivninger_nok_per_ar": 0.0, "finanskostnader_nok_per_ar": 0.0, "skattesats_pct": 0.22}
DCF_DEFAULTS = {"rf_pct": 0.04, "mp_pct": 0.05, "beta": 0.80, "horisont_ar": 10, "ev_ebitda": 8.0}

# Sesongindeks (brukes kun hvis "Sesongvariert" velges - post-smolt selges
# normalt til fast tabellpris). Samme tall som Big Dipper.
from config_1tank import SEASONAL_PRICE_INDEX_BY_WEEK_RAW, SEASONAL_PRICE_INDEX_BY_WEEK  # noqa: E402

OUTPUT_DIR = "output"
