"""
config_postsmolt.py - Post-smolt landanlegg: post-smolt til Aqualoop Big Dipper
------------------------------------------------------------------------------
Leverandøren produserer post-smolt (750 g) på land (RAS) og leverer til
Norway Offshore Salmon (NOS) sine Big Dipper-enheter (ABD). Denne filen er
landanleggsmodellens motstykke til config_1tank.py i FFM Big Dipper - samme
struktur og samme navn på feltene resource_ledger.py trenger, slik at
ledger/P&L/balanse fungerer uendret nedstrøms.

HOVEDFORSKJELLER fra Big Dipper-konfigen:
  1. Kohortene plasseres BAKOVER fra ABD sin innsettuke (= leverandørens
     leveringsuke), ikke fremover fra en tankrotasjon. Se
     scheduler_postsmolt.py.
  2. Kapasitet sjekkes per TRINN (yngel / smolt / post-smolt / evt. fase 2)
     med et karpool (antall kar x m3) og et tetthetstak per trinn - ikke
     per fast tank. Fisken tilhører trinnet etter VEKT.
  3. Produkttype er alltid Postsmolt (WFE, ingen slakt/distribusjon).
  4. leverandøren EIER anlegget selv - ingen SFaaS/utleier-lag.

Kilder: leverandørens presentasjon (anleggsdata fase 1, CAPEX og finansieringsplan), samtale 11.09.2026 (rogn, vaksine, 0,10 kr/g,
tetthetstak 50 kg/m3, 850 000 stk a 750 g per ABD-leveranse).
"""

# ----------------------------------------------------------------------
# 1. LEVERANSEPLAN - hva som skal UT (styrt av ABD-innsettene hos NOS)
# ----------------------------------------------------------------------
N_ABD = 1                              # antall Big Dipper-enheter landanlegget leverer til (1 eller 2).
                                       # BEKREFTET av bruker: én ABD i første omgang; nr. 2 krever fase 2-hallen.
LEVERANSER_PER_ABD_PER_AR = 6          # 6 innsett per ABD per år (jan/mar/mai/jul/sep/nov i Big Dipper-modellen)
ABD_INNSETT_MANEDER = {                # kalendermåneder for innsett per ABD - første MANDAG i måneden,
    1: [1, 3, 5, 7, 9, 11],            # samme regel som "Jevnt fordelt over året" i streamlit_app_1tank.py
    2: [2, 4, 6, 8, 10, 12],           # ABD 2 forskjøvet én måned - gir 12 jevne leveranser i året
}
LEVERT_ANTALL_PER_LEVERANSE = 850_000  # BEKREFTET av bruker (= smoltantall per kohort i Big Dipper-modellen)
LEVERT_VEKT_KG = 0.750                 # BEKREFTET av bruker: 750 g (leverandørens eget design er 560 g)
INNSETT_ANTALL_MODUS = "auto"          # "auto": innsettantall ved 30 g regnes ut fra dødelighet slik at
                                       # LEVERT_ANTALL_PER_LEVERANSE faktisk leveres; "fast": bruk INNSETT_ANTALL_FAST
INNSETT_ANTALL_FAST = 875_000
INNSETT_ANTALL_AVRUNDING = 1_000       # rund innsettantall opp til nærmeste tusen

LEVERANSE_START_AR = 2026              # første ABD-innsett landanlegget leverer til (BEKREFTET av bruker 12.09.2026:
                                       # 2026, slik at tallene er sammenlignbare med Big Dipper-modellen). Første
                                       # innsett på landanlegget ligger vekstuker FØR dette = juli 2025.
N_YEARS_TO_RUN = 12                    # leveranseår 2026-2037 - samme horisont som SFaaS/Konsolidert (bekreftet 12.09.2026)
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
# (fôr, strøm, lønn) - byttes ut når leverandøren har egne tall.
ROGN_KR_PER_STK = 3.75
VAKSINE_KR_PER_STK = 6.25
SMOLT_PRICE_BASE_KR = ROGN_KR_PER_STK + VAKSINE_KR_PER_STK   # 10,00
SMOLT_PRICE_PER_GRAM_KR = 0.10
SMOLT_PRIS_MODUS = "Formel"            # leverandøren lager yngelen selv -> selvkost-formel, IKKE fiskeverditabellen

# ----------------------------------------------------------------------
# 3. TRINN (karpooler) - fase 1 landanlegget, leverandørens anleggsdata
#    Fisken tilhører trinnet etter vekt: vekt_fra_g <= vekt < vekt_til_g.
#    Siste aktive trinn er åpent oppover (tar fisken helt til levering).
#    Tetthetstak: BEKREFTET av bruker 50 kg/m3 (post-smolt). Yngel/smolt
#    er ANTAKELSER - juster når RAS-leverandørens design foreligger.
#    NB: tegningen s. 11 sier 8 smoltkar, tabellen sier 10 - bruker 8
#    (BEKREFTET av bruker).
# ----------------------------------------------------------------------
TRINN = [
    {"id": "yngel",     "navn": "Yngel (ferskvann)",      "vekt_fra_g": 0,    "vekt_til_g": 69,
     "antall_kar": 14, "kar_volum_m3": 191, "tetthetstak_kg_m3": 40.0, "aktiv": True},
    {"id": "smolt",     "navn": "Smolt (saltvann)",       "vekt_fra_g": 69,   "vekt_til_g": 157,
     "antall_kar": 7,  "kar_volum_m3": 883, "tetthetstak_kg_m3": 50.0, "aktiv": True},
    {"id": "postsmolt", "navn": "Post-smolt",             "vekt_fra_g": 157,  "vekt_til_g": None,
     "antall_kar": 14, "kar_volum_m3": 1_575, "tetthetstak_kg_m3": 50.0, "aktiv": True},
    # Fase 2 storsmolt-hall (30 x 3 165 m3 = 94 950 m3). Slås på for ABD nr. 2:
    # da tar fase 1-hallen fisken til FASE2_OVERGANG_G og fase 2 resten.
    {"id": "fase2",     "navn": "Storsmolt (fase 2)",     "vekt_fra_g": None, "vekt_til_g": None,
     "antall_kar": 30, "kar_volum_m3": 3_165, "tetthetstak_kg_m3": 50.0, "aktiv": False},
]
# ----------------------------------------------------------------------
# 3b. ANLEGGSDESIGN - ferdige oppsett som kan velges i appen (setter N_ABD,
#     karpooler, CAPEX og banklån samlet). "som tegnet" = leverandørens fase 1;
#     Fase I.A / I.B = NOS sitt eget forslag (notat 12.09.2026): post-smolt-kar
#     på 1 575 m3 (Ø20 m, H5 m) slik at én kohort på 850 000 x 0,741 kg ved
#     50 kg/m3 = 12 600 m3 fyller nøyaktig 8 kar ved levering.
#     CAPEX: Fase I.A = 1 475 MNOK (bekreftet av bruker); Fase I.B = I.A +
#     15 750 m3 à 40 000 kr/m3 = 2 105 MNOK. Banklån 50 %.
# ----------------------------------------------------------------------
ANLEGG_DESIGN = {
    "Leverandørens fase 1 (som tegnet)": {
        "n_abd": 1,
        "trinn": {"yngel": (14, 191, 40.0), "smolt": (8, 883, 50.0), "postsmolt": (24, 883, 50.0)},
        "capex_nok": 1_472_788_000.0, "banklan_nok": 739_000_000.0,
        "operatorer_antall": 6, "biologi_antall": 2, "vedlikehold_nok_per_ar": 5_000_000.0, "salgspris_kr_kg": 100.0,
        "beskrivelse": "Fase 1 slik den er tegnet: 24 post-smolt-kar à 883 m³. Én ABD, 6 leveranser/år. 23/24 kar på topp.",
    },
    "Fase I.A – 14 × 1 575 m³ (én ABD)": {
        "n_abd": 1,
        "trinn": {"yngel": (14, 191, 40.0), "smolt": (7, 883, 50.0), "postsmolt": (14, 1_575, 50.0)},
        "capex_nok": 1_475_000_000.0, "banklan_nok": 740_000_000.0,
        "operatorer_antall": 6, "biologi_antall": 2, "vedlikehold_nok_per_ar": 5_000_000.0,
        "salgspris_kr_kg": 100.0,       # BEKREFTET 12.09.2026
        # Kar 15-24 i post-smolt-hallen bygges først i Fase I.B: tegnes svarte og
        # er IKKE tilgjengelige for kapasiteten i I.A.
        "reservert": {"postsmolt": (10, "Fase I.B")},
        "beskrivelse": "NOS-forslag, trinn 1: 14 post-smolt-kar à 1 575 m³ (8 kar per kohort ved levering). Én ABD, 6 leveranser/år. CAPEX 1 475 MNOK (BEKREFTET av bruker 12.09.2026). Kar 15–24 er reservert Fase I.B (svarte i tegningen).",
    },
    "Fase I.B – 24 × 1 575 m³ (to ABD, fra dag én)": {
        # BEKREFTET 12.09.2026: I.B kjøres som om hele modulen bygges fra start.
        # CAPEX = I.A 1 475 + tilleggs-CAPEX 500 MNOK; samme belåningsgrad (50 %);
        # 40 års avskrivning; variable enhetskostnader uendret; vedlikehold og
        # forsikring følger CAPEX; bemanning 10 operatører + 3 biologer.
        "n_abd": 2,
        "trinn": {"yngel": (14, 191, 40.0), "smolt": (7, 883, 50.0), "postsmolt": (24, 1_575, 50.0)},
        "capex_nok": 1_975_000_000.0, "banklan_nok": 987_500_000.0,
        "operatorer_antall": 10, "biologi_antall": 3,
        "salgspris_kr_kg": 85.0,        # BEKREFTET 12.09.2026: I.B leverer til 85 kr/kg (dobbelt volum, lavere pris)
        "vedlikehold_nok_per_ar": 5_000_000.0 * 1_975 / 1_475,   # skalert med CAPEX (~6,7 MNOK)
        "beskrivelse": "NOS-forslag, full modul fra dag én: 24 post-smolt-kar à 1 575 m³, to ABD-er, 12 leveranser/år (forskjøvet én måned). CAPEX 1 475 + 500 = 1 975 MNOK, lån 50 %. Bemanning 10 + 3.",
    },
}
DEFAULT_ANLEGG_DESIGN = "Fase I.A – 14 × 1 575 m³ (én ABD)"   # BEKREFTET av bruker 12.09.2026: modellen kjører på I.A

FASE2_OVERGANG_G = 400                 # når fase 2 er aktiv: fisk >= dette flyttes fra fase 1 post-smolt til fase 2
KAR_DELES_IKKE_MELLOM_KOHORTER = True  # biosikkerhet: et kar rommer kun én kohort -> karbehov rundes OPP per kohort

# Kompatibilitet med 1-tank-motoren/ledgeren (brukes ikke som tak i landanlegget):
TANK_VOLUME_M3 = 883
MAX_DENSITY_KG_M3 = 50.0               # BEKREFTET av bruker

# ----------------------------------------------------------------------
# 4. DØDELIGHET / VEKST / TEMPERATUR (RAS - styrt, konstant)
# ----------------------------------------------------------------------
ANNUAL_MORTALITY_PCT = 5.8377          # 0,5 %/mnd (samme som Big Dipper) - ANTAKELSE for RAS, ofte lavere
RGI_PCT = 100.0
TEMPERATURE_PROFILES = {
    "landanlegget RAS 12 °C (konstant)": [12.0] * 12,     # leverandørens anleggsdata: 12 °C smolt/post-smolt
    "landanlegget RAS 10 °C (konstant)": [10.0] * 12,
    "landanlegget RAS 14 °C (konstant)": [14.0] * 12,
}
DEFAULT_TEMPERATURE_PROFILE = "landanlegget RAS 12 °C (konstant)"
MONTHLY_TEMPERATURES_C = TEMPERATURE_PROFILES[DEFAULT_TEMPERATURE_PROFILE]

# ----------------------------------------------------------------------
# 5. KALENDER - settes av scheduleren ut fra leveranseplanen:
#    uke 0 = første innsett på landanlegget (= første ABD-innsett minus vekstuker).
#    Verdiene her er kun fallback når filen kjøres utenom appen.
# ----------------------------------------------------------------------
START_ISO_YEAR = 2025
START_ISO_WEEK = 29

# ----------------------------------------------------------------------
# 6. RESSURSREGNSKAP - RAS-drivere. Samme id-er som Big Dipper der det er
#    mulig, slik at resource_ledger.py og appens kolonneoppsett kan gjenbrukes.
#    "kg WFE" = bruttovekst den uken (samme prinsipp som Big Dipper).
# ----------------------------------------------------------------------
RESOURCES = [
    # Struktur bekreftet 12.09.2026 (samme nummerering 0-12 som Big Dipper; 7 og 9 nye for RAS).
    # Priser kalibrert mot Samonix (AquaMaof RAS 10 000 t, KPMG-plan mai 2024, 2029-tall,
    # CAD 7,5): COGS 45 NOK/kg WFE = fôr 28, andre 5,8, produksjonslønn 4,6, strøm 3,5,
    # rogn 1,1, forsikring 1,0, pH 0,8, avløp 0,5. Post-smolt (750 g) har dyrere fôr
    # (småpellet) og mer håndtering per kg enn matfisk, og norsk strømpris ~3x Québec.
    {"id": "smolt",                  "navn": "0. Rogn, vaksine og yngel (30 g)", "enhet": "stk",    "kilde": "smolt"},
    {"id": "for",                    "navn": "1. Fôrforbruk",                    "enhet": "kg",     "kilde": "feed"},
    {"id": "energi",                 "navn": "2. Strøm (RAS)",                   "enhet": "kWh",    "kilde": "wfe", "faktor_per_kg_wfe": 7.0},   # BEKREFTET 12.09.2026 (Samonix 11,8 kWh/kg inkl. slakteri)
    {"id": "oksygen",                "navn": "3. Oksygen",                       "enhet": "kg O2",  "kilde": "wfe", "faktor_per_kg_wfe": 0.55},  # BEKREFTET 12.09.2026
    {"id": "vann",                   "navn": "4. Vann, salt og pH-justering",    "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "annet_direkte_material", "navn": "5. Annet direkte materiell (forbruk, filter, diagnostikk)", "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "annet_direkte_lonn",     "navn": "6. Andre direkte lønnskostnader (variabel)", "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "avlop",                  "navn": "7. Avløp og slam",                 "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},   # NY (Samonix: waste/wastewater)
    {"id": "distribusjon",           "navn": "8. Distribusjon (brønnbåt til ABD)", "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "fiskehelse",             "navn": "9. Fiskehelse og veterinær",       "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},   # NY
    {"id": "indirekte_material",     "navn": "10. Indirekte materiell",          "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "indirekte_lonn",         "navn": "11. Indirekte lønn",               "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
    {"id": "andre_produksjon",       "navn": "12. Forsikring av biomasse",       "enhet": "kg WFE", "kilde": "wfe", "faktor_per_kg_wfe": 1.0},
]
WFE_FAKTOR = 1.0

RESOURCE_PRICES_NOK = {
    "smolt": None,                    # formel (SMOLT_PRICE_*): 13,00 kr/stk ved 30 g
    "for": 17.0,                      # kr/kg fôr - BEKREFTET 12.09.2026 (Samonix impliserer ~25 kr/kg for matfisk)
    "energi": 1.0,                    # kr/kWh - BEKREFTET 12.09.2026 (Samonix 0,30)
    "oksygen": 5.0,                   # kr/kg O2 - BEKREFTET 12.09.2026
    "vann": 1.5,                      # kr/kg WFE - salt, pH-justering (Samonix 0,8 + vann); var 0,5
    "annet_direkte_material": 3.0,    # kr/kg WFE - forbruksmateriell, filter, diagnostikk (Samonix "other costs" 5,8); var 1,0
    "annet_direkte_lonn": 1.0,        # kr/kg WFE - variabel del av produksjonslønn (fast del i 14); var 2,0
    "avlop": 0.5,                     # kr/kg WFE - slam/avløp (Samonix 0,5) - NY
    "distribusjon": 0.0,              # 0 = NOS henter med brønnbåt
    "fiskehelse": 0.5,                # kr/kg WFE - veterinær, prøvetaking (del av Samonix "other costs") - NY
    "indirekte_material": 1.0,        # kr/kg WFE; var 0,5
    "indirekte_lonn": 0.5,
    "andre_produksjon": None,          # formel, se BIOMASSEFORSIKRING_DEFAULTS (Samonix ~1,0 kr/kg)
}
BIOMASSEFORSIKRING_DEFAULTS = {"andel_av_salgspris_pct": 0.50, "forsikringssats_pct": 0.03}

# ----------------------------------------------------------------------
# 7. SALG - post-smolt selges som WFE til NOS. Prisen følger
#    fiskeverditabellen (samme tabell som NOS bruker for "0. Kjøpt smolt"),
#    slik at inntekt hos leverandøren = kostnad hos NOS, krone for krone.
#    750 g -> 85,2 kr/kg -> ca. 64 kr/stk.
# ----------------------------------------------------------------------
PRODUKTTYPE = "Postsmolt"
HOG_FAKTOR = 1.0
SALGSPRIS_MODUS = "Fast pris (kr/kg)"          # BEKREFTET 12.09.2026: fast pris, ikke fiskeverditabellen (høyere CAPEX må dekkes)
POSTSMOLT_SALGSPRIS_KR_PER_KG = 100.0          # kr/kg WFE - BEKREFTET 12.09.2026; brukes også som innkjøpspris i SFaaS/Konsolidert ("Eget post-smolt-anlegg")
SMOLT_VERDITABELL_KR_PER_KG = [
    (60, 266.7), (100, 245.9), (150, 219.0), (200, 186.9), (250, 156.1),
    (300, 132.0), (350, 114.3), (400, 101.1), (500, 91.8), (600, 86.9),
    (700, 85.2), (800, 85.0), (900, 85.0), (1000, 85.0),
]
USE_SEASONAL_PRICE_INDEX = False

# ----------------------------------------------------------------------
# 8. FASTE KOSTNADER 13-16 - landanlegget EIER anlegget (ingen leie).
#    Struktur bekreftet 12.09.2026:
#      13. Anleggskostnader (eget anlegg): 13.1 Vedlikehold, 13.2 Rengjøring,
#          13.3 Desinfeksjon (per kohort), 13.4 Forsikring anlegg (% av CAPEX),
#          13.5 Eiendomsskatt og tomt, 13.6 ADK anlegg
#      14. Produksjonslønn (fast): 14.1 Driftsoperatører, 14.2 Biologi/kvalitet, 14.3 Sosiale %
#      15. Administrasjon: 15.1 Adm. ansatte, 15.2 Revisjon/jus/rådgivning, 15.3 Kontor/IT/reise
#      16. Salg og logistikk
#    CAPEX, avskrivning og renter ligger under EBITDA (UTLEIER_DEFAULTS/"Eier").
#    Kalibrert mot Samonix 2029 (10 000 t): vedlikehold 0,4 % av CAPEX, forsikring
#    ~9 MNOK totalt, SG&A 33 MNOK (7 % av opex), produksjonslønn 44 MNOK/9 500 t.
# ----------------------------------------------------------------------
ANLEGG_DEFAULTS = {
    "capex_nok": 1_475_000_000.0,             # CAPEX Fase I.A (bekreftet 12.09.2026)
    "vedlikehold_nok_per_ar": 5_000_000.0,    # 13.1 - BEKREFTET 12.09.2026 (0,34 % av CAPEX; Samonix 0,4 %)
    "rengjoring_nok_per_ar": 2_000_000.0,     # 13.2 - kar, rør, biofilter
    "desinfeksjon_nok_per_kohort": 25_000.0,  # 13.3 - hendelsesbasert ved innsett (BEKREFTET 12.09.2026, var 100 000)
    "forsikring_pct_capex": 0.0025,           # 13.4 - 0,25 % av CAPEX (BEKREFTET 12.09.2026)
    "eiendomsskatt_tomt_nok_per_ar": 2_000_000.0,   # 13.5 - ANTAKELSE (Samonix: property tax + land maintenance ~4 % av SG&A)
    "adk_anlegg_nok_per_ar": 3_000_000.0,     # 13.6 - ANTAKELSE
}
PRODUKSJONSLONN_DEFAULTS = {
    "operatorer_antall": 6,                   # 14.1 - BEKREFTET av bruker 12.09.2026 (var 18)
    "operatorer_lonn_nok": 1_100_000.0,
    "biologi_antall": 2,                      # 14.2 - biologi/kvalitet/fiskehelse (BEKREFTET 12.09.2026, var 3)
    "biologi_lonn_nok": 1_400_000.0,
    "sosiale_kostnader_pct": 0.32,            # 14.3
}
ADMINISTRASJON_DEFAULTS = {
    "adm_antall": 3,                          # 15.1 - ledelse, økonomi, HR
    "adm_lonn_nok": 1_800_000.0,
    "sosiale_kostnader_pct": 0.32,
    "revisjon_jus_radgivning_nok_per_ar": 2_000_000.0,   # 15.2
    "kontor_it_reise_nok_per_ar": 2_000_000.0,           # 15.3
}
SALG_LOGISTIKK_NOK_PER_AR = 0.0               # 16 - 0 hvis NOS henter fisken med egen brønnbåt

# Appen gjenbruker Big Dipper sitt "13. Leie"-maskineri med kapitalleie 0 -
# disse nøklene må derfor finnes (verdiene hentes fra ANLEGG_DEFAULTS over).
HEXACAGE_LEIE_DEFAULTS = {
    "capex_nok": ANLEGG_DEFAULTS["capex_nok"],
    "kapitalleie_pct": 0.0, "oppankring_investering_nok": 0.0, "oppankring_nedbetaling_maneder": 60, "oppankring_rente_pct_ar": 0.0,
    "teknisk_vedlikehold_nok_per_ar": ANLEGG_DEFAULTS["vedlikehold_nok_per_ar"],
    "rengjoring_innvendig_nok_per_ar": ANLEGG_DEFAULTS["rengjoring_nok_per_ar"],
    "rengjoring_krager_nok_per_ar": 0.0,
    "desinfeksjon_nok_per_kohort": ANLEGG_DEFAULTS["desinfeksjon_nok_per_kohort"],
    "lonn_lokalitet_nok_per_ar": 0.0, "lonn_lokalitet_antall": 0, "lonn_land_nok_per_ar": 0.0, "lonn_land_antall": 0,
    "sosiale_kostnader_pct": 0.32,
    "forsikring_pct": ANLEGG_DEFAULTS["forsikring_pct_capex"],
    "adk_nok_per_ar": ANLEGG_DEFAULTS["adk_anlegg_nok_per_ar"],
}
FIXED_COSTS = [
    {"id": "leie_anlegg",    "navn": "13. Anleggskostnader (eget anlegg)"},
    {"id": "prodlonn",       "navn": "14. Produksjonslønn (fast)"},
    {"id": "administrasjon", "navn": "15. Administrasjon"},
    {"id": "salg_logistikk", "navn": "16. Salg og logistikk"},
]
FIXED_COST_KR_PER_UKE = {
    "leie_anlegg": None,      # sum av 13.1-13.6 (regnes i appen)
    "prodlonn": None,         # regnes fra PRODUKSJONSLONN_DEFAULTS i appen
    "administrasjon": None,   # regnes fra ADMINISTRASJON_DEFAULTS i appen
    "salg_logistikk": SALG_LOGISTIKK_NOK_PER_AR / 52.0,
}

# ----------------------------------------------------------------------
# 9. EIER - leverandøren som eget selskap (leverandørens investeringsplan, finansieringsplan fase 1)
#    Appen bruker UTLEIER_DEFAULTS-nøklene i "Konsolidert"-maskineriet; for
#    landanlegget settes banklånet DIREKTE (BANKLAN_NOK), ikke som x EBITDA.
# ----------------------------------------------------------------------
BANKLAN_NOK = 740_000_000.0               # 50 % av CAPEX Fase I.A (leverandørens plan: lån 739 / EK 733 MNOK)
UTLEIER_DEFAULTS = {
    "ebitda_multipel": 0.0,
    "swap_rente_pct": 0.04,
    "kredittpaslag_pct": 0.035,
    "banklan_nedbetaling_ar": 12,
    "skattesats_pct": 0.22,
    "vedlikeholdsinvestering_nok_forste_ar": 5_000_000.0,    # BEKREFTET 12.09.2026 (var 10)
    "vedlikeholdsinvestering_pct_capex": 0.0068,
    "vedlikeholdsinvestering_indeksering_pct_ar": 0.02,
    "avskrivningstid_ar": 40,             # BEKREFTET av bruker 12.09.2026 (som ABD)
    "refi_intervall_ar": 0,
    "refi_multipel": 0.0,
    "terminal_ebitda_multipel": 12.0,
    "holding_years": 10,
}
RESULTAT_DEFAULTS = {"avskrivninger_nok_per_ar": 0.0, "finanskostnader_nok_per_ar": 0.0, "skattesats_pct": 0.22}
DCF_DEFAULTS = {"rf_pct": 0.04, "mp_pct": 0.05, "beta": 0.60, "horisont_ar": 10, "ev_ebitda": 12.0}   # BEKREFTET 12.09.2026: beta 0,6 (r = 7 %), sluttverdi 12x EBITDA

# Sesongindeks (brukes kun hvis "Sesongvariert" velges - post-smolt selges
# normalt til fast tabellpris). Samme tall som Big Dipper.
from config_1tank import SEASONAL_PRICE_INDEX_BY_WEEK_RAW, SEASONAL_PRICE_INDEX_BY_WEEK  # noqa: E402

OUTPUT_DIR = "output"
