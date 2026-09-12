"""
ffm_headless.py - kjører ffm_big_dipper.py UTEN brukergrensesnitt og skriver
nøkkeltall som JSON til stdout. Brukes av visningen "Oppsummering" i appen,
som starter dette skriptet som underprosess én gang per scenario (SFaaS,
Konsolidert, Post-smolt landanlegg) og tegner en samlet figur av resultatene.

Bruk:  python ffm_headless.py '<json>'
  json = {"view": "SFaaS oppdrett" | "Konsolidert" | "Post-smolt landanlegg",
          "session": {...}      # valgfrie session_state-nøkler som settes FØR kjøring
          "design": "..."       # valgfritt: anleggsdesign (landanlegg) - kalles via preset-callback
          "salgspris": 85}      # valgfritt: salgspris kr/kg for landanlegget (settes etter design)

Streamlit erstattes av en minimal stub: hver widget returnerer sin standardverdi
(eller verdien i session_state hvis satt). Ingen tegning, ingen nettverk.
"""
from __future__ import annotations
import json
import runpy
import sys
import os
import types
import contextlib
import io as _io

import pandas as pd


# ----------------------------------------------------------------------
# Minimal streamlit-stub
# ----------------------------------------------------------------------
class _SS(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


def _make_stub():
    st = types.ModuleType("streamlit")
    st.session_state = _SS()

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __getattr__(self, name):
            return getattr(st, name)

    class column_config:
        @staticmethod
        def CheckboxColumn(*a, **k):
            return None

        @staticmethod
        def NumberColumn(*a, **k):
            return None

        @staticmethod
        def TextColumn(*a, **k):
            return None

    st.column_config = column_config

    def _widget(key, default):
        if key is not None and key in st.session_state:
            return st.session_state[key]
        if key is not None:
            st.session_state[key] = default
        return default

    st.radio = lambda label, options, index=0, key=None, **k: _widget(key, list(options)[index])
    st.selectbox = lambda label, options, index=0, key=None, **k: _widget(key, list(options)[index])

    def number_input(label, value=0.0, key=None, min_value=None, max_value=None, **k):
        v = _widget(key, value)
        if max_value is not None and v > max_value:
            v = max_value
        if min_value is not None and v < min_value:
            v = min_value
        return v

    st.number_input = number_input
    st.text_input = lambda label, value="", key=None, **k: ("" if _widget(key, value) is None else str(_widget(key, value)))
    st.text_area = lambda label, value="", key=None, **k: _widget(key, value)
    st.checkbox = lambda label, value=False, key=None, **k: _widget(key, value)
    st.toggle = lambda label, value=False, key=None, **k: _widget(key, value)
    st.slider = lambda label, min_value=0, max_value=100, value=None, key=None, **k: _widget(key, value if value is not None else min_value)
    st.date_input = lambda label, value=None, key=None, **k: _widget(key, value)
    st.multiselect = lambda label, options, default=None, key=None, **k: _widget(key, default or [])
    st.data_editor = lambda df, key=None, **k: (df.copy() if isinstance(df, pd.DataFrame) else df)
    st.button = lambda *a, **k: False
    st.download_button = lambda *a, **k: False
    st.form_submit_button = lambda *a, **k: False
    st.columns = lambda spec, **k: [_Ctx() for _ in range(spec if isinstance(spec, int) else len(spec))]
    st.tabs = lambda names, **k: [_Ctx() for _ in names]
    st.expander = lambda *a, **k: _Ctx()
    st.container = lambda *a, **k: _Ctx()
    st.form = lambda *a, **k: _Ctx()
    st.spinner = lambda *a, **k: _Ctx()

    class _Empty(_Ctx):
        pass

    st.empty = lambda: _Empty()
    st.sidebar = _Ctx()
    for n in ("title", "caption", "markdown", "write", "header", "subheader", "info", "warning", "error", "success",
              "metric", "dataframe", "table", "pyplot", "plotly_chart", "line_chart", "bar_chart", "area_chart",
              "image", "divider", "code", "json", "latex", "html", "progress", "set_page_config"):
        setattr(st, n, lambda *a, **k: None)
    st.rerun = lambda: (_ for _ in ()).throw(RuntimeError("rerun"))
    st.stop = lambda: (_ for _ in ()).throw(SystemExit)

    def _passthrough(f=None, **k):
        return (lambda g: g) if f is None else f

    st.cache_resource = _passthrough
    st.cache_data = _passthrough
    st.fragment = _passthrough
    comps = types.ModuleType("streamlit.components")
    v1 = types.ModuleType("streamlit.components.v1")
    v1.html = lambda *a, **k: None
    v1.iframe = lambda *a, **k: None
    comps.v1 = v1
    st.components = comps
    sys.modules["streamlit"] = st
    sys.modules["streamlit.components"] = comps
    sys.modules["streamlit.components.v1"] = v1
    return st


# ----------------------------------------------------------------------
# Kjøring og uttrekk
# ----------------------------------------------------------------------
def _year_last(balanse: pd.DataFrame) -> pd.DataFrame:
    b = balanse.copy()
    b["_ar"] = pd.to_datetime(b["dato"]).dt.isocalendar().year
    return b.groupby("_ar").last()


def run(spec: dict) -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)
    if here not in sys.path:
        sys.path.insert(0, here)
    st = _make_stub()
    st.session_state["produkttype_valg"] = spec["view"]
    for k, v in (spec.get("session") or {}).items():
        st.session_state[k] = v
    with contextlib.redirect_stdout(_io.StringIO()), contextlib.redirect_stderr(_io.StringIO()):
        g = runpy.run_path("ffm_big_dipper.py", run_name="__main__")
        if spec.get("design"):
            st.session_state["ps_design"] = spec["design"]
            g["_bruk_anleggsdesign"]()
            if spec.get("salgspris") is not None:
                st.session_state["salgspris"] = str(spec["salgspris"])
                st.session_state["salgspris_modus"] = "Fast pris (kr/kg)"
            g = runpy.run_path("ffm_big_dipper.py", run_name="__main__")
        elif spec.get("salgspris") is not None and spec["view"] == "Post-smolt landanlegg":
            st.session_state["salgspris"] = str(spec["salgspris"])
            st.session_state["salgspris_modus"] = "Fast pris (kr/kg)"
            g = runpy.run_path("ffm_big_dipper.py", run_name="__main__")

    res = g["_dcf_res"]
    bal = _year_last(g["balanse"])
    years = [int(y) for y in res.index]
    y2 = years[1] if len(years) > 1 else years[0]
    out = {
        "view": spec["view"],
        "operator": {
            "capex": float(g["_ik_capex"]), "lan": float(g["_ik_lan"]),
            "ek_invest": float(g["_ik_ek_inv"]), "ek_oper": float(g["_ik_ek_oper"]), "uses": float(g["_ik_uses"]),
            "years": years, "ebitda": [float(res.loc[y, "ebitda_kr"]) for y in years],
            "ebitda_y2": float(res.loc[y2, "ebitda_kr"]),
            "pv_fcff": float(g["_pv_fcff"]), "pv_tv": float(g["_pv_tv"]), "ev": float(g["_pv_fcff"] + g["_pv_tv"]),
            "r": float(g["dcf_r"]), "mult": float(g["dcf_multippel"]), "ebitda_N1": float(g["_ebitda_N1"]),
            "nibd_y2": float(bal.loc[y2, "banklan"] - bal.loc[y2, "kontanter"]) if y2 in bal.index else float(g["_ik_lan"]),
            "salgspris_kr_kg": float(getattr(g["cfg"], "SALES_PRICE_KR_PER_KG", 0.0) or 0.0),
            "kg_solgt_y2": float(res.loc[y2, "kg_solgt"]) if "kg_solgt" in res.columns else 0.0,
            "solgt_enhet": "HOG" if getattr(g["cfg"], "PRODUKTTYPE", "") == "Slaktefisk" else "WFE",
            "smolt_kr_stk": float(g.get("computed_smolt_price", 0.0) or 0.0),
        },
    }
    if not g["konsolidert"] and "utleier_regnskap" in g:
        ur = g["utleier_regnskap"]["resultat"].set_index("periode")
        uy = [int(y) for y in ur.index]
        uy2 = uy[1] if len(uy) > 1 else uy[0]
        out["utleier"] = {
            "capex": float(g["capex"]), "lan": float(g["utleier_regnskap"]["banklan_belop_kr"]), "uses": float(g["_ut_uses"]),
            "years": uy, "ebitda": [float(x) for x in ur["ebitda_kr"]], "ebitda_y2": float(ur.loc[uy2, "ebitda_kr"]),
            "pv_fcff": float(g["_u_pv_fcff"]), "pv_tv": float(g["_u_pv_tv"]), "ev": float(g["_u_ev"]),
            "r": float(g["dcf_r"]), "mult": float(g["dcf_multippel"]), "ebitda_N1": float(g["_u_ebitda_N1"]),
            "kapitalleie_pct": float(g["kapitalleie_pct"]),
        }
    return out


if __name__ == "__main__":
    spec = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {"view": "SFaaS oppdrett"}
    print(json.dumps(run(spec), default=float))
