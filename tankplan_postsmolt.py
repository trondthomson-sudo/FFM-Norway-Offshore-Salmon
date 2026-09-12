"""
tankplan_postsmolt.py - kar-tildeling per kohort og tegning av anlegget
------------------------------------------------------------------------------
Lag oppå scheduler_postsmolt.py. Scheduleren regner hvor mange kar hver
kohort trenger per uke i hvert trinn (info["kar_behov_per_uke"]); denne
modulen tildeler KONKRETE kar (Y1.., S1.., P1..) etter "lavest ledige
nummer", slik at:
  - et kar aldri deles mellom to kohorter (biosikkerhet), og
  - et kar som tildeles en kohort er ledig i HELE perioden kohorten trenger
    det i det trinnet (ellers kunne en senere kohort blokkert et kar som en
    tidligere kohort skulle vokse inn i).
Vaskeuker etter levering holder karene i siste trinn opptatt.

Gir (a) en flyttetabell per kohort (dato, hall, antall kar, kar-ID-er, vekt,
biomasse), (b) en belegg-matrise kar x uke, og (c) en tegning av anlegget
måned for måned med kohortene fargelagt - samme figur som i notatet
"Post-smolt til Big Dipper", men generert fra modellens egen plan.
"""

from __future__ import annotations
import math
import datetime as dt
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle, Patch

from scheduler_1tank import monday_of_week

PREFIX = {"yngel": "Y", "smolt": "S", "postsmolt": "P", "fase2": "F"}


def tildel_kar(cfg, generations: dict, cohorts: list, meta: dict):
    """Returnerer (tanks, moves):
    tanks: {kar_id: [kohort_id eller "" per uke]} over hele horisonten
    moves: {kohort_id: [(dato, trinn_id, antall_kar, [kar_ider]), ...]}"""
    trinn = {t["id"]: t for t in meta["trinn"]}
    n_weeks = meta["n_weeks_total"]
    uke_dato = [monday_of_week(w, cfg.START_ISO_YEAR, cfg.START_ISO_WEEK) for w in range(n_weeks)]
    tanks = {f"{PREFIX.get(tid, tid[0].upper())}{i + 1}": [""] * n_weeks
             for tid, t in trinn.items() for i in range(int(t["antall_kar"]))}
    order = sorted(generations.items(), key=lambda kv: kv[1]["start_week"])
    moves = {gid: [] for gid, _ in order}
    for gid, info in order:
        tp, kb, sw = info["trinn_per_uke"], info["kar_behov_per_uke"], info["start_week"]
        i = 0
        while i < len(tp):
            stage = tp[i]
            j = i
            while j < len(tp) and tp[j] == stage:
                j += 1
            w0, w1 = sw + i, sw + j - 1
            if j == len(tp):
                w1 += int(info["cleaning_weeks"])
            w1 = min(w1, n_weeks - 1)
            pre = PREFIX.get(stage, stage[0].upper())
            cur = []
            for wk in range(w0, w1 + 1):
                k = kb[wk - sw] if wk - sw < len(kb) else len(cur)
                if k > len(cur):
                    free = sorted(
                        [t for t in tanks if t[0] == pre and t not in cur
                         and all(tanks[t][x] == "" for x in range(wk, w1 + 1))],
                        key=lambda t: int(t[1:]))
                    new = free[:k - len(cur)]
                    cur += new
                    moves[gid].append((uke_dato[wk], stage, k, list(cur), len(new) < k - len(cur)))
                for t in cur:
                    tanks[t][wk] = gid if wk - sw < len(kb) else f"({gid}) vask"
            i = j
    return tanks, moves, uke_dato


def kohortplan_tabell(cfg, generations, cohorts, meta, tanks, moves, uke_dato) -> pd.DataFrame:
    trinn = {t["id"]: t for t in meta["trinn"]}
    coh = {c.id: c for c in cohorts}
    rows = []
    for gid, info in sorted(generations.items(), key=lambda kv: kv[1]["start_week"]):
        c = coh[gid]
        tp = info["trinn_per_uke"]
        for (d, stage, k, tl, mangler) in moves[gid]:
            i = (d - uke_dato[info["start_week"]]).days // 7
            if i == 0:
                hendelse = "Innsett"
            elif i > 0 and tp[i - 1] != stage:
                hendelse = "Flytt til " + trinn[stage]["navn"]
            else:
                hendelse = "Splitt til flere kar"
            if mangler:
                hendelse += " (FOR FÅ KAR)"
            rows.append({
                "Kohort": gid, "Hendelse": hendelse, "Dato": d,
                "Uke": f"{d.isocalendar()[0]}-U{d.isocalendar()[1]:02d}",
                "Hall": trinn[stage]["navn"], "Antall kar": k, "Kar": ", ".join(tl),
                "Vekt (g)": round(c.weekly_weight_kg[min(i, len(c.weekly_weight_kg) - 1)] * 1000),
                "Biomasse (t)": round(c.weekly_standing_biomass_kg[min(i, len(c.weekly_standing_biomass_kg) - 1)] / 1000, 1),
            })
        d = info["leveringsdato"]
        rows.append({
            "Kohort": gid, "Hendelse": "Levering til Big Dipper", "Dato": d,
            "Uke": f"{d.isocalendar()[0]}-U{d.isocalendar()[1]:02d}",
            "Hall": trinn[tp[-1]]["navn"], "Antall kar": info["kar_behov_per_uke"][-1],
            "Kar": ", ".join(moves[gid][-1][3]) if moves[gid] else "",
            "Vekt (g)": round(info["delivery_weight_kg"] * 1000),
            "Biomasse (t)": round(info["delivered_biomass_kg"] / 1000, 1),
        })
    return pd.DataFrame(rows)


def belegg_matrise(tanks, uke_dato, fra: dt.date | None = None, til: dt.date | None = None) -> pd.DataFrame:
    idx = [w for w, d in enumerate(uke_dato) if (fra is None or d >= fra) and (til is None or d < til)]
    cols = [f"{uke_dato[w].isocalendar()[0]}-U{uke_dato[w].isocalendar()[1]:02d}" for w in idx]
    return pd.DataFrame({c: [tanks[t][w] for t in tanks] for c, w in zip(cols, idx)}, index=list(tanks))


# ----------------------------------------------------------------------
# Tegning
# ----------------------------------------------------------------------
PALETT = [("#c0522b", "#e8b4a0"), ("#2f5d8a", "#a9c1dc"), ("#3a8a5c", "#b5d9c2"), ("#8e5aa8", "#d3bfe0"),
          ("#b8860b", "#e8d39a"), ("#6b4c3b", "#c9b3a8"), ("#1f7a8c", "#a8d3db"), ("#a33a3a", "#e0b0b0")]


def _hall_layout(meta, reservert: dict | None = None):
    """Plassering av hallene i tegningen: én rad av bokser, bredde etter antall
    kar. `reservert` = {trinn_id: (antall, etikett)} - kar som tegnes svarte
    (bygges i senere fase, ikke tilgjengelige nå)."""
    reservert = reservert or {}
    halls = []
    x = 0.5
    for t in meta["trinn"]:
        n_res, res_lbl = reservert.get(t["id"], (0, ""))
        n_aktiv = int(t["antall_kar"])
        n, vol = n_aktiv + int(n_res), float(t["kar_volum_m3"])
        r = 0.55 * (vol / 883.0) ** 0.5
        r = max(0.45, min(r, 1.3))
        per_row = n if n <= 8 else math.ceil(n / 2)
        rows = -(-n // per_row)
        gap = 0.3
        bw = per_row * (2 * r + gap) + gap
        bh = rows * (2 * r + gap) + gap
        halls.append({"id": t["id"], "navn": t["navn"], "n": n, "n_aktiv": n_aktiv, "res_lbl": res_lbl,
                      "vol": vol, "r": r, "per_row": per_row, "rows": rows, "x": x, "bw": bw, "bh": bh, "gap": gap})
        x += bw + 0.8
    maks_h = max(h["bh"] for h in halls)
    return halls, x, maks_h


def tegn_tankbruk(cfg, generations, cohorts, meta, tanks, uke_dato, kohorter: list[str],
                  tittel: str | None = None):
    """Måned-for-måned-tegning av anlegget for valgte kohorter (maks 8).
    Andre kohorter vises grå. Returnerer matplotlib-figur."""
    farge = {k: PALETT[i % len(PALETT)] for i, k in enumerate(kohorter)}
    coh = {c.id: c for c in cohorts}
    trinn = {t["id"]: t for t in meta["trinn"]}
    n_weeks = len(uke_dato)
    first = min(generations[k]["start_week"] for k in kohorter)
    last = min(max(generations[k]["delivery_week"] for k in kohorter) + 1, n_weeks - 1)
    snaps = set([generations[k]["start_week"] for k in kohorter] + [generations[k]["delivery_week"] for k in kohorter])
    for w in range(first, last + 1):
        if w + 1 > last or uke_dato[w + 1].month != uke_dato[w].month:
            snaps.add(w)
    snaps = sorted(s for s in snaps if s < n_weeks)
    halls, tot_w, maks_h = _hall_layout(meta, getattr(cfg, "RESERVERTE_KAR", None))

    def state(t, w):
        v = tanks.get(t, [""])[min(w, len(tanks.get(t, [""])) - 1)] if t in tanks else None
        if v is None:
            return "#222222", "#bbbbbb"     # reservert (senere fase)
        for k, (f, fv) in farge.items():
            if v == k:
                return f, "white"
            if v.startswith(f"({k})"):
                return fv, "#555"
        if v:
            return "#dddddd", "#888"
        return "white", "#777"

    def draw(ax, w):
        ax.set_xlim(0, tot_w)
        ax.set_ylim(-1.6, maks_h + 0.9)
        ax.set_aspect("equal")
        ax.axis("off")
        for h in halls:
            pre = PREFIX.get(h["id"], h["id"][0].upper())
            ax.add_patch(Rectangle((h["x"], 0), h["bw"], h["bh"], fill=False, lw=1.1, ec="#c0392b"))
            _lbl = f"{h['navn'].split(' (')[0]} · {h['n_aktiv']} × {h['vol']:,.0f} m³".replace(",", " ")
            if h["n"] > h["n_aktiv"]:
                _lbl += f" (+{h['n'] - h['n_aktiv']} svarte: {h['res_lbl']})"
            ax.text(h["x"], -0.25, _lbl, fontsize=8, va="top")
            for i in range(h["n"]):
                rr, cc = divmod(i, h["per_row"])
                cx = h["x"] + h["gap"] + h["r"] + cc * (2 * h["r"] + h["gap"])
                cy = h["bh"] - h["gap"] - h["r"] - rr * (2 * h["r"] + h["gap"])
                fc, tc = state(f"{pre}{i + 1}", w)
                ax.add_patch(Circle((cx, cy), h["r"], fc=fc, ec="#555", lw=0.6))
                ax.text(cx, cy, str(i + 1), ha="center", va="center", fontsize=6.5, color=tc)

    n = len(snaps)
    cols = 3 if n <= 9 else 4
    rows = math.ceil(n / cols)
    panel_w = 6.0
    panel_h = panel_w * (maks_h + 2.5) / tot_w + 1.1   # plass til tittel-linjene
    fig, axes = plt.subplots(rows, cols, figsize=(panel_w * cols, panel_h * rows))
    axes = [axes] if n == 1 else list(axes.flatten())
    for ax, w in zip(axes, snaps):
        draw(ax, w)
        d = uke_dato[w]
        ev = []
        for k in kohorter:
            navn = k.split("-")[1] if "-" in k else k
            if w == generations[k]["start_week"]:
                ev.append(f"{navn} innsett")
            if w == generations[k]["delivery_week"]:
                ev.append(f"{navn} LEVERES")
        tit = d.strftime("%d.%m.%Y") + (" – " + ", ".join(ev) if ev else f" (slutten av {d.strftime('%b %Y')})")
        lines = []
        for k in kohorter:
            info, c = generations[k], coh[k]
            i = w - info["start_week"]
            navn = k.split("-")[1] if "-" in k else k
            if 0 <= i < len(c.weekly_weight_kg):
                lines.append(f"{navn}: {c.weekly_weight_kg[i] * 1000:.0f} g · {c.weekly_standing_biomass_kg[i] / 1000:.0f} t · {info['kar_behov_per_uke'][i]} kar")
            elif i == len(c.weekly_weight_kg):
                lines.append(f"{navn}: vask")
        siste = meta["trinn"][-1]
        pre = PREFIX.get(siste["id"], siste["id"][0].upper())
        pbruk = sum(1 for t in tanks if t[0] == pre and tanks[t][w])
        lines.append(f"{siste['navn'].split(' (')[0]}: {pbruk}/{siste['antall_kar']} kar i bruk")
        ax.set_title(tit + "\n" + "\n".join(lines), fontsize=9, loc="left")
    for ax in axes[n:]:
        ax.axis("off")
    handles = []
    for k, (f, fv) in farge.items():
        handles.append(Patch(fc=f, label=f"{k} (lev. {generations[k]['leveringsdato'].strftime('%d.%m.%Y')})"))
    handles.append(Patch(fc="#dddddd", label="andre kohorter"))
    handles.append(Patch(fc="#eeeeee", label="vask (lys farge)"))
    if any(h["n"] > h["n_aktiv"] for h in halls):
        handles.append(Patch(fc="#222222", label="reservert senere fase"))
    fig.legend(handles=handles, loc="lower right", fontsize=9.5, ncol=min(len(handles), 5))
    fig.suptitle(tittel or f"Tankbruk måned for måned – {', '.join(kohorter)}", fontsize=11)
    fig.tight_layout(rect=(0, 0.04, 1, 0.97))
    return fig


def tegn_anlegg_maanedlig(cfg, generations, cohorts, meta, tanks, uke_dato, n_maaneder: int = 24,
                          start_uke: int | None = None):
    """Motstykket til Big Dipper sine kakediagrammer: anlegget ved slutten av
    hver måned, ALLE kohorter fargelagt (fargen følger kohortens rekkefølge),
    med kar i bruk / kar totalt per hall i panel-tittelen. Vaskede kar i lys
    farge. Returnerer matplotlib-figur."""
    halls, tot_w, maks_h = _hall_layout(meta, getattr(cfg, "RESERVERTE_KAR", None))
    order = [gid for gid, _ in sorted(generations.items(), key=lambda kv: kv[1]["start_week"])]
    farge = {gid: PALETT[i % len(PALETT)] for i, gid in enumerate(order)}
    n_weeks = len(uke_dato)
    w0 = start_uke if start_uke is not None else min(info["start_week"] for info in generations.values())
    # siste uke i hver måned fra w0
    snaps = []
    for w in range(w0, n_weeks):
        if w + 1 >= n_weeks or uke_dato[w + 1].month != uke_dato[w].month:
            snaps.append(w)
        if len(snaps) >= n_maaneder:
            break

    def state(t, w):
        if t not in tanks:
            return "#222222", "#bbbbbb"     # reservert (senere fase)
        v = tanks[t][w]
        if not v:
            return "white", "#777"
        if v.startswith("("):
            gid = v[1:].split(")")[0]
            return farge.get(gid, ("#ccc", "#eee"))[1], "#555"
        return farge.get(v, ("#ccc", "#eee"))[0], "white"

    cols = 3 if n_maaneder > 6 else n_maaneder
    rows = math.ceil(len(snaps) / cols)
    panel_w = 7.4
    panel_h = panel_w * (maks_h + 2.5) / tot_w + 0.9
    fig, axes = plt.subplots(rows, cols, figsize=(panel_w * cols, panel_h * rows))
    axes = [axes] if len(snaps) == 1 else list(axes.flatten())
    mnd = ["Jan", "Feb", "Mar", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Des"]
    for ax, w in zip(axes, snaps):
        ax.set_xlim(0, tot_w)
        ax.set_ylim(-1.6, maks_h + 0.9)
        ax.set_aspect("equal")
        ax.axis("off")
        bruk = []
        for h in halls:
            pre = PREFIX.get(h["id"], h["id"][0].upper())
            ax.add_patch(Rectangle((h["x"], 0), h["bw"], h["bh"], fill=False, lw=1.1, ec="#c0392b"))
            _lbl = h["navn"].split(" (")[0]
            if h["n"] > h["n_aktiv"]:
                _lbl += f" (svarte kar = {h['res_lbl']})"
            ax.text(h["x"], -0.25, _lbl, fontsize=8.5, va="top")
            i_bruk = 0
            for i in range(h["n"]):
                rr, cc = divmod(i, h["per_row"])
                cx = h["x"] + h["gap"] + h["r"] + cc * (2 * h["r"] + h["gap"])
                cy = h["bh"] - h["gap"] - h["r"] - rr * (2 * h["r"] + h["gap"])
                fc, tc = state(f"{pre}{i + 1}", w)
                if f"{pre}{i + 1}" in tanks and tanks[f"{pre}{i + 1}"][w]:
                    i_bruk += 1
                ax.add_patch(Circle((cx, cy), h["r"], fc=fc, ec="#555", lw=0.6))
                ax.text(cx, cy, str(i + 1), ha="center", va="center", fontsize=6.5, color=tc)
            bruk.append(f"{h['navn'].split(' (')[0][:10]} {i_bruk}/{h['n_aktiv']}")
        d = uke_dato[w]
        ax.set_title(f"{mnd[d.month - 1]} {d.year} · " + " · ".join(bruk), fontsize=11, fontweight="bold", loc="left")
    for ax in axes[len(snaps):]:
        ax.axis("off")
    aktive = [gid for gid in order if any(tanks[t][w] in (gid, f"({gid}) vask") for t in tanks for w in snaps)]
    handles = [Patch(fc=farge[g][0], label=f"{g} (lev. {generations[g]['leveringsdato'].strftime('%d.%m.%y')})") for g in aktive[:16]]
    if any(h["n"] > h["n_aktiv"] for h in halls):
        handles.append(Patch(fc="#222222", label="svart = reservert senere fase (ikke tilgjengelig)"))
    fig.legend(handles=handles, loc="lower center", fontsize=9.5, ncol=min(6, max(1, len(handles))), frameon=False)
    fig.suptitle(f"Anlegget måned for måned ({uke_dato[snaps[0]].strftime('%b %Y')}–{uke_dato[snaps[-1]].strftime('%b %Y')}): "
                 f"hvert kar farget etter kohort, lys farge = vask, hvitt = ledig. Tittel viser kar i bruk per hall.", fontsize=12)
    fig.tight_layout(rect=(0, 0.06, 1, 0.97))
    return fig


def tegn_anleggskart(design_a: dict, design_b: dict, navn_a: str = "Fase I.A", navn_b: str = "Fase I.B",
                     trinn_navn: dict | None = None):
    """Ett statisk kart over anlegget: alle kar i design_b tegnet, der karene
    som IKKE finnes i design_a er mørke (= utvidelsen fra A til B). Brukes
    til å vise doblingen fra én til to ABD-er i samme bilde."""
    trinn_navn = trinn_navn or {"yngel": "Yngel 8–69 g", "smolt": "Smolt 69–157 g", "postsmolt": "Post-smolt / storsmolt 157–750 g"}
    halls = []
    x = 0.5
    for tid, (n_b, vol, tak) in design_b["trinn"].items():
        n_a = design_a["trinn"].get(tid, (0, vol, tak))[0]
        r = max(0.45, min(0.55 * (vol / 883.0) ** 0.5, 1.3))
        per_row = n_b if n_b <= 8 else math.ceil(n_b / 2)
        rows = -(-n_b // per_row)
        gap = 0.3
        bw = per_row * (2 * r + gap) + gap
        bh = rows * (2 * r + gap) + gap
        halls.append(dict(id=tid, n_a=n_a, n_b=n_b, vol=vol, tak=tak, r=r, per_row=per_row, x=x, bw=bw, bh=bh, gap=gap))
        x += bw + 0.8
    tot_w, maks_h = x, max(h["bh"] for h in halls)
    fig, ax = plt.subplots(figsize=(16, 16 * (maks_h + 3.2) / tot_w))
    ax.set_xlim(0, tot_w)
    ax.set_ylim(-2.0, maks_h + 0.6)
    ax.set_aspect("equal")
    ax.axis("off")
    for h in halls:
        ax.add_patch(Rectangle((h["x"], 0), h["bw"], h["bh"], fill=False, lw=1.3, ec="#c0392b"))
        for i in range(h["n_b"]):
            rr, cc = divmod(i, h["per_row"])
            cx = h["x"] + h["gap"] + h["r"] + cc * (2 * h["r"] + h["gap"])
            cy = h["bh"] - h["gap"] - h["r"] - rr * (2 * h["r"] + h["gap"])
            ny = i >= h["n_a"]
            ax.add_patch(Circle((cx, cy), h["r"], fc="#222222" if ny else "#e39a86", ec="#444", lw=0.8))
            ax.text(cx, cy, str(i + 1), ha="center", va="center", fontsize=8, color="#dddddd" if ny else "#333")
        vol_a, vol_b = h["n_a"] * h["vol"], h["n_b"] * h["vol"]
        lbl = f"{trinn_navn.get(h['id'], h['id'])}\n{navn_a}: {h['n_a']} kar · {vol_a:,.0f} m³"
        if h["n_b"] > h["n_a"]:
            lbl += f"\n{navn_b}: +{h['n_b'] - h['n_a']} kar (svarte) → {h['n_b']} kar · {vol_b:,.0f} m³"
        ax.text(h["x"], -0.3, lbl.replace(",", " "), fontsize=9.5, va="top")
    fig.legend(handles=[Patch(fc="#e39a86", ec="#444", label=f"{navn_a} – bygges først (én ABD)"),
                        Patch(fc="#222222", ec="#444", label=f"{navn_b} – utvidelse (to ABD)")],
               loc="lower center", fontsize=10, frameon=False, ncol=2)
    sum_a = sum(design_a["trinn"][t][0] * v for t, (n, v, k) in design_b["trinn"].items() if t in design_a["trinn"])
    sum_b = sum(n * v for n, v, k in design_b["trinn"].values())
    def _sp(v):
        return f"{v:,.0f}".replace(",", " ")
    fig.suptitle(f"Anleggskart: {navn_a} → {navn_b}.   {navn_a}: {_sp(sum_a)} m³ karvolum (ekskl. startfôring), CAPEX {_sp(design_a['capex_nok'] / 1e6)} MNOK, "
                 f"én ABD – 5,1 mill. post-smolt/år.   {navn_b}: {_sp(sum_b)} m³, CAPEX {_sp(design_b['capex_nok'] / 1e6)} MNOK, "
                 f"to ABD – 10,2 mill./år.", fontsize=11)
    fig.tight_layout(rect=(0, 0.08, 1, 0.94))
    return fig
