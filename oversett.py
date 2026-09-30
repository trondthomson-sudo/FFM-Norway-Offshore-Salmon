"""
oversett.py - språklag for appen (Norsk / English)
------------------------------------------------------------------------------
Modellen er skrevet på norsk. Når brukeren velger "English", oversettes ALT
som vises (tekst, tabeller, grafer, feltnavn, hjelpetekster og valgene i
radioknapper/nedtrekkslister) i det øyeblikket det tegnes - selve modellen,
variabler og interne verdier forblir norske, så ingen beregning påvirkes.

Slik virker det:
  * Hver tekst gjøres om til en MAL: alle tall byttes med {0}, {1}, ... og
    månedsnavn med {M0}, {M1}, ... Malen slås opp i ordlisten
    oversettelser_en.json (norsk mal -> engelsk mal), og tallene settes inn
    igjen. Da dekker én oppføring alle varianter av en f-streng med tall.
  * Tall skrives på engelsk format: mellomrom som tusenskille blir komma
    (1 234 567 -> 1,234,567). Desimaltegn er punktum i begge språk.
  * Tekst som ikke finnes i ordlisten får en enkel ord-for-ord-erstatning
    fra ORDLISTE (under), og registreres i en liste over mangler når
    miljøvariabelen FFM_OVERSETT_SAMLE peker på en fil (brukes for å
    oppdatere ordlisten).

Nye tekster i appen: kjør appen med FFM_OVERSETT_SAMLE=mangler.json og legg
de nye malene inn i oversettelser_en.json.
"""

from __future__ import annotations
import json
import os
import re

SPRAK = "no"   # "no" eller "en" - settes av appen

_HER = os.path.dirname(os.path.abspath(__file__))
_ORDBOK_FIL = os.path.join(_HER, "oversettelser_en.json")
_ORDBOK: dict | None = None
_SAMLE = os.environ.get("FFM_OVERSETT_SAMLE")
_MANGLER: dict = {}

_MND_NO = ["januar", "februar", "mars", "april", "mai", "juni", "juli", "august", "september", "oktober",
           "november", "desember"]
_MND_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]
_MND_KORT_NO = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"]
_MND_KORT_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Tall: "1 234 567", "1 234.5", "-12.75", "2026" (mellomrom eller hardt mellomrom som tusenskille)
_TALL = re.compile(r"(?<!\d)(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:\.\d+)?(?![\d])|\d+(?:\.\d+)?)")
_BOKSTAV = re.compile(r"[A-Za-zÆØÅæøå]")
_MND_RE = re.compile(r"(?<![A-Za-zÆØÅæøå])(" + "|".join(sorted(_MND_NO + _MND_KORT_NO, key=len, reverse=True))
                     + r")(?![A-Za-zÆØÅæøå])", re.IGNORECASE)


def _last_ordbok() -> dict:
    global _ORDBOK
    if _ORDBOK is None:
        try:
            with open(_ORDBOK_FIL, encoding="utf-8") as f:
                _ORDBOK = json.load(f)
        except Exception:
            _ORDBOK = {}
    return _ORDBOK


def lag_mal(s: str):
    """(mal, tall, måneder): tall -> {0}.., månedsnavn -> {M0}.."""
    tall, mnd = [], []

    def _t(m):
        tall.append(m.group(0))
        return "{" + str(len(tall) - 1) + "}"

    mal = _TALL.sub(_t, s)

    def _m(m):
        mnd.append(m.group(0))
        return "{M" + str(len(mnd) - 1) + "}"

    mal = _MND_RE.sub(_m, mal)
    return mal, tall, mnd


def _tall_en(t: str) -> str:
    return re.sub(r"[   ]", ",", t)


def _mnd_en(m: str) -> str:
    lav = m.lower()
    if lav in _MND_NO:
        return _MND_EN[_MND_NO.index(lav)]
    if lav in _MND_KORT_NO:
        return _MND_KORT_EN[_MND_KORT_NO.index(lav)]
    return m


def _fyll(mal_en: str, tall, mnd) -> str:
    def _t(m):
        i = int(m.group(1))
        return _tall_en(tall[i]) if i < len(tall) else m.group(0)

    ut = re.sub(r"\{(\d+)\}", _t, mal_en)

    def _m(m):
        i = int(m.group(1))
        return _mnd_en(mnd[i]) if i < len(mnd) else m.group(0)

    return re.sub(r"\{M(\d+)\}", _m, ut)


def oversett(s):
    """Norsk tekst -> engelsk (uendret når SPRAK == 'no')."""
    if SPRAK == "no" or not isinstance(s, str) or not s.strip():
        return s
    # behold ledende/etterfølgende mellomrom (viktig i HTML-tekstnoder)
    ledende = s[: len(s) - len(s.lstrip())]
    etter = s[len(s.rstrip()):]
    kjerne = s.strip()
    mal, tall, mnd = lag_mal(kjerne)
    if not _BOKSTAV.search(re.sub(r"\{M?\d+\}", "", mal)):
        return ledende + _fyll(mal, tall, mnd) + etter        # bare tall/tegn: kun tallformat
    ordbok = _last_ordbok()
    en = ordbok.get(mal)
    if en is None:
        if _SAMLE:
            _MANGLER[mal] = _MANGLER.get(mal, 0) + 1
        en = _ordvis(mal)
    return ledende + _fyll(en, tall, mnd) + etter


# ---- Reserve: ord for ord for tekster som ikke finnes i ordboken ----
ORDLISTE = {
    "Kohort": "Cohort", "kohort": "cohort", "Kohorter": "Cohorts", "kohorter": "cohorts",
    "Tank": "Tank", "tanker": "tanks", "Uke": "Week", "uke": "week", "uker": "weeks", "Uker": "Weeks",
    "År": "Year", "år": "year", "Måned": "Month", "måned": "month", "Hele": "Full", "Maks": "Max",
    "Sum": "Total", "Ledig": "Free", "ledig": "free", "vask": "cleaning", "Vask": "Cleaning",
    "Vekst": "Growth", "Rengjoring": "Cleaning", "stk": "pcs", "kr": "NOK", "og": "and", "per": "per",
    "Skott": "Compartment", "skott": "compartment", "Innsett": "Stocking", "brukt": "used",
    "Biomasse": "Biomass", "biomasse": "biomass", "tetthet": "density", "Tetthet": "Density",
    "Levert": "Delivered", "levert": "delivered", "Solgt": "Sold", "solgt": "sold",
    "Inntekt": "Revenue", "inntekt": "revenue", "Kostnad": "Cost", "kostnad": "cost",
    "Fôr": "Feed", "fôr": "feed", "Smolt": "Smolt", "Yngel": "Fry", "Post-smolt": "Post-smolt",
    "Anlegg": "Facility", "anlegg": "facility", "totalt": "total", "Totalt": "Total",
}
_ORD_RE = re.compile(r"(?<![A-Za-zÆØÅæøå])(" + "|".join(sorted(map(re.escape, ORDLISTE), key=len, reverse=True))
                     + r")(?![A-Za-zÆØÅæøå])")


def _ordvis(mal: str) -> str:
    return _ORD_RE.sub(lambda m: ORDLISTE[m.group(0)], mal)


_TAG = re.compile(r"(<[^>]+>)")


def oversett_html(html: str) -> str:
    """Oversetter kun tekstnodene i en HTML-streng (tagger/stiler røres ikke)."""
    if SPRAK == "no" or not isinstance(html, str):
        return html
    deler = _TAG.split(html)
    i_stil = False
    for i, d in enumerate(deler):
        if d.startswith("<"):
            lav = d.lower()
            if lav.startswith("<style") or lav.startswith("<script"):
                i_stil = True
            elif lav.startswith("</style") or lav.startswith("</script"):
                i_stil = False
            continue
        if not i_stil and d.strip():
            deler[i] = oversett(d)
    return "".join(deler)


def oversett_markdown(s):
    """Markdown/HTML: HTML-tekstnoder oversettes hver for seg; ren markdown som
    helhet (malen tar med ** osv.)."""
    if SPRAK == "no" or not isinstance(s, str):
        return s
    if "<" in s and ">" in s:
        return oversett_html(s)
    return oversett(s)


def lagre_mangler():
    """Skriver ikke-oversatte maler (med antall treff) til FFM_OVERSETT_SAMLE."""
    if not _SAMLE:
        return
    try:
        gammel = {}
        if os.path.exists(_SAMLE):
            with open(_SAMLE, encoding="utf-8") as f:
                gammel = json.load(f)
        for k, v in _MANGLER.items():
            gammel[k] = gammel.get(k, 0) + v
        with open(_SAMLE, "w", encoding="utf-8") as f:
            json.dump(gammel, f, ensure_ascii=False, indent=0)
    except Exception:
        pass
