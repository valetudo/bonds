"""Controlli Streamlit: impostazioni globali e form del ladder."""
from __future__ import annotations

from typing import Optional, Tuple

import streamlit as st

from ladder.builder import LadderParams


def global_controls() -> dict:
    """Sidebar con i toggle globali che pilotano il ricalcolo YTM."""
    st.sidebar.header("⚙️ Impostazioni")
    use_net = st.sidebar.toggle("Mostra/usa YTM netto (post-imposte)", value=True)
    apply_bollo = st.sidebar.toggle("Includi imposta di bollo (0,2%/anno)", value=False)
    freq = st.sidebar.selectbox(
        "Frequenza cedola assunta", options=[1, 2], index=0,
        format_func=lambda x: "Annuale" if x == 1 else "Semestrale",
        help="Modalità 'Bilanciata': la frequenza è assunta (non letta dalle schede).",
    )
    daycount = st.sidebar.selectbox("Convenzione day-count", options=["ACT/ACT", "30/360"], index=0)
    st.sidebar.caption("YTM sempre calcolato dai prezzi correnti, mai scaricato.")
    return {"use_net": use_net, "apply_bollo": apply_bollo, "freq": freq, "daycount": daycount}


def ladder_form(settings: dict) -> Tuple[Optional[LadderParams], int]:
    """Form parametri ladder con validazione somma allocazioni = 100%.
    Ritorna (LadderParams|None, totale_allocazioni)."""
    c1, c2, c3 = st.columns(3)
    capital = c1.number_input("Capitale (€)", min_value=1000.0, value=50000.0, step=1000.0)
    n_steps = c2.number_input("Numero gradini", min_value=1, max_value=30, value=10, step=1)
    max_dur = c3.number_input("Durata massima (anni)", min_value=1, max_value=50, value=10, step=1)

    st.markdown("**Allocazione per categoria** (la somma deve fare 100%)")
    a1, a2, a3, a4 = st.columns(4)
    gi = a1.number_input("gov_ita %", min_value=0, max_value=100, value=50, step=5)
    ci = a2.number_input("corp_ita %", min_value=0, max_value=100, value=20, step=5)
    ge = a3.number_input("gov_eur %", min_value=0, max_value=100, value=20, step=5)
    ce = a4.number_input("corp_eur %", min_value=0, max_value=100, value=10, step=5)

    tot = int(gi + ci + ge + ce)
    if tot == 100:
        st.success(f"Totale allocazioni: {tot}%")
    else:
        st.error(f"Totale allocazioni: {tot}% — deve essere 100%")

    params: Optional[LadderParams] = None
    if tot == 100:
        params = LadderParams(
            capital=float(capital), n_steps=int(n_steps), max_duration_years=float(max_dur),
            alloc_gov_ita=float(gi), alloc_corp_ita=float(ci),
            alloc_gov_eur=float(ge), alloc_corp_eur=float(ce),
            use_net_yield=bool(settings.get("use_net", True)),
            apply_bollo=bool(settings.get("apply_bollo", False)),
        )
    return params, tot
