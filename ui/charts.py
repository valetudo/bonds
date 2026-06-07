"""Grafici Plotly (funzioni pure → go.Figure)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

CATEGORY_COLORS = {
    "gov_ita": "#2a7abf",
    "corp_ita": "#e28743",
    "gov_eur": "#2f855a",
    "corp_eur": "#7b61ff",
}
CATEGORY_LABELS = {
    "gov_ita": "Gov Italia",
    "corp_ita": "Corp Italia",
    "gov_eur": "Gov estero",
    "corp_eur": "Corp estero",
}

_HOVER = (
    "<b>%{customdata[0]}</b> · %{customdata[1]}"
    "<br>cedola %{customdata[2]:.2f}% · prezzo %{customdata[3]:.2f}"
    "<br>YTM lordo %{customdata[4]:.2f}% · netto %{customdata[5]:.2f}%"
    "<br>paese %{customdata[6]}<extra></extra>"
)


def scatter_yield_vs_maturity(df: pd.DataFrame, use_net: bool = True) -> go.Figure:
    """Scatter YTM (%) vs anni alla scadenza, una traccia per categoria."""
    ycol = "ytm_netto" if use_net else "ytm_lordo"
    fig = go.Figure()
    for cat, color in CATEGORY_COLORS.items():
        if "categoria" not in df.columns:
            break
        sub = df[(df["categoria"] == cat) & df[ycol].notna() & df["anni_scadenza"].notna()]
        if sub.empty:
            continue
        cd = sub[["isin", "descrizione", "cedola_pct", "prezzo",
                  "ytm_lordo", "ytm_netto", "paese"]].fillna(0)
        fig.add_trace(go.Scatter(
            x=sub["anni_scadenza"], y=sub[ycol], mode="markers",
            name=CATEGORY_LABELS.get(cat, cat),
            marker=dict(color=color, size=7, opacity=0.72, line=dict(width=0.5, color="#fff")),
            customdata=cd.to_numpy(),
            hovertemplate=_HOVER,
        ))
    for x in (3, 7):
        fig.add_vline(x=x, line=dict(dash="dash", color="#cccccc", width=1))
    fig.update_layout(
        xaxis_title="Anni alla scadenza",
        yaxis_title=f"YTM {'netto' if use_net else 'lordo'} (%)",
        legend_title="Categoria", height=520,
        margin=dict(l=10, r=10, t=30, b=10),
        hovermode="closest",
    )
    return fig


def stacked_bar_ladder(table: pd.DataFrame) -> go.Figure:
    """Stacked bar: X = fascia anni, Y = importo €, stack per categoria."""
    fig = go.Figure()
    if table is None or table.empty:
        fig.update_layout(height=380)
        return fig
    fasce = list(dict.fromkeys(table["fascia_anni"].tolist()))
    for cat, color in CATEGORY_COLORS.items():
        sub = table[table["categoria"] == cat]
        if sub.empty:
            continue
        by = sub.groupby("fascia_anni")["importo_eur"].sum()
        y = [float(by.get(f, 0.0)) for f in fasce]
        fig.add_trace(go.Bar(x=fasce, y=y, name=CATEGORY_LABELS.get(cat, cat),
                             marker_color=color))
    fig.update_layout(
        barmode="stack", xaxis_title="Fascia (anni)", yaxis_title="Importo (€)",
        legend_title="Categoria", height=420, margin=dict(l=10, r=10, t=30, b=10),
    )
    return fig
