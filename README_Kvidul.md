# FFM-Kvidul – oppsett av ny mappe (steg 1: produksjonsplan)

Ny, separat mappe. **Ingen endringer** i FFM Big Dipper eller FFM-Hexacage.

## 1. Lag mappen og kopier UENDRET fra FFM Big Dipper
    FFM-Kvidul/
      data/fcr_table.csv          (kopi)
      data/sgr_table.csv          (kopi)
      growth_tables.py            (kopi)
      simulator.py                (kopi)
      temperature.py              (kopi)
      scheduler_1tank.py          (kopi – Cohort-klassen og ukehjelperne gjenbrukes)
      resource_ledger.py          (kopi – fungerer uendret på Kvidul-outputen, verifisert)
      formatting.py               (kopi)
      requirements.txt            (kopi – husk scipy)

## 2. Nye filer (leveres her)
      config_kvidul.py            leveranseplan, trinn/karpooler, 30 g-selvkost, RAS-drivere, eier-defaults
      scheduler_kvidul.py         kohorter bakover fra ABD-innsett, kapasitet per trinn

## 3. Kjør sjekk
    python scheduler_kvidul.py
Forventet (defaults, 1 ABD, 12 °C): 25 vekstuker, 875 000 inn -> 850 126 ut à 741 g,
630 t per leveranse, post-smolt-hallen 23/24 kar på topp, 0 uker over kapasitet.

## 4. Scenarioer i config_kvidul.py
- N_ABD = 2 uten fase 2: 39 kar trengs i post-smolt (24 finnes) -> går ikke.
- N_ABD = 2 med TRINN["fase2"]["aktiv"] = True: fase 1 13/24 kar, fase 2 8/30 kar -> god margin.

## 5. Neste steg (steg 2/3)
- streamlit_app_kvidul.py: kopi av streamlit_app_1tank.py der Slaktefisk/utleier-lag og
  Postsmolt 2x/3x/4x-presets fjernes, scheduler_multitank byttes med scheduler_kvidul,
  og sidepanelet får trinn-tabellen (kar, volum, tetthetstak) + leveranseplan (N_ABD).
- Eier-regnskap: gjenbruk build_eier_uke (Konsolidert-maskineriet) med EIER_DEFAULTS.

## Antakelser merket ANTAKELSE i config_kvidul.py som Kvidul bør bekrefte
tetthetstak yngel/smolt, strøm 6 kWh/kg og 1 kr/kWh, oksygen 0,5 kg/kg og 3 kr/kg,
dødelighet 0,5 %/mnd i RAS, faste kostnader, avskrivningstid 25 år.
