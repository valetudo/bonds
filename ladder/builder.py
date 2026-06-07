"""Costruzione del bond ladder.

Disaccoppiato dalla sorgente dati: riceve un DataFrame già pronto (universo +
colonne YTM calcolate da finance/). Divide l'orizzonte in fasce temporali
contigue uguali e, per ogni (fascia × categoria), seleziona il bond con YTM più
alto allocando il capitale secondo le percentuali richieste, in lotti interi.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

import config

CATEGORIE = ("gov_ita", "corp_ita", "gov_eur", "corp_eur")

OUTPUT_COLUMNS = [
    "gradino", "fascia_anni", "isin", "descrizione", "categoria", "paese",
    "scadenza", "cedola_pct", "ytm_lordo", "ytm_netto", "prezzo",
    "importo_eur", "n_titoli_nominale",
]

# Colonne richieste nell'input (YTM in %).
REQUIRED_INPUT = [
    "isin", "descrizione", "categoria", "paese", "scadenza", "cedola_pct",
    "prezzo", "anni_scadenza", "ytm_lordo", "ytm_netto",
]


@dataclass
class LadderParams:
    capital: float
    n_steps: int
    max_duration_years: float
    alloc_gov_ita: float
    alloc_corp_ita: float
    alloc_gov_eur: float
    alloc_corp_eur: float
    use_net_yield: bool = True
    apply_bollo: bool = False
    lot_nominal: float = config.LOTTO_NOMINALE

    def alloc(self) -> Dict[str, float]:
        return {
            "gov_ita": self.alloc_gov_ita,
            "corp_ita": self.alloc_corp_ita,
            "gov_eur": self.alloc_gov_eur,
            "corp_eur": self.alloc_corp_eur,
        }

    def alloc_sum(self) -> float:
        return round(sum(self.alloc().values()), 6)

    def validate(self) -> Optional[str]:
        """Ritorna un messaggio d'errore o None se i parametri sono validi.
        Le allocazioni possono essere in percentuale (somma 100) o frazioni (1)."""
        if self.capital <= 0:
            return "Il capitale deve essere > 0."
        if self.n_steps < 1:
            return "Il numero di gradini deve essere >= 1."
        if self.max_duration_years <= 0:
            return "La durata massima deve essere > 0."
        s = self.alloc_sum()
        if abs(s - 100.0) > 1e-6 and abs(s - 1.0) > 1e-6:
            return f"Le allocazioni devono sommare a 100% (attuale: {s:g})."
        if any(v < 0 for v in self.alloc().values()):
            return "Le allocazioni non possono essere negative."
        return None


@dataclass
class LadderResult:
    table: pd.DataFrame
    warnings: List[str]
    weighted_ytm_gross: float
    weighted_ytm_net: float
    capital_allocated: float
    n_bonds: int


def _weighted_avg(table: pd.DataFrame, col: str) -> float:
    if table.empty or col not in table:
        return 0.0
    w = table["importo_eur"]
    v = table[col]
    mask = v.notna() & w.notna() & (w > 0)
    if not mask.any():
        return 0.0
    return float((v[mask] * w[mask]).sum() / w[mask].sum())


def build_ladder(universe_with_ytm: pd.DataFrame, params: LadderParams) -> LadderResult:
    """Costruisce il ladder. Non solleva su fasce/categorie vuote: le segnala
    in `warnings`."""
    warnings: List[str] = []
    err = params.validate()
    if err:
        raise ValueError(err)

    missing = [c for c in REQUIRED_INPUT if c not in universe_with_ytm.columns]
    if missing:
        raise ValueError(f"Colonne mancanti nell'universo: {missing}")

    yield_col = "ytm_netto" if params.use_net_yield else "ytm_lordo"

    df = universe_with_ytm.copy()
    df = df[df["prezzo"].notna() & df["anni_scadenza"].notna() & df[yield_col].notna()]
    df = df[(df["anni_scadenza"] > 0) & (df["anni_scadenza"] <= params.max_duration_years)]
    df = df.reset_index(drop=True)

    alloc = params.alloc()
    total_alloc = sum(alloc.values()) or 1.0
    weights = {k: v / total_alloc for k, v in alloc.items()}
    step = params.max_duration_years / params.n_steps
    per_rung = params.capital / params.n_steps

    rows: List[dict] = []
    for i in range(params.n_steps):
        lo, hi = i * step, (i + 1) * step
        fascia = f"{lo:.1f}-{hi:.1f}"
        band = df[(df["anni_scadenza"] > lo) & (df["anni_scadenza"] <= hi)]
        for cat in CATEGORIE:
            w = weights.get(cat, 0.0)
            if w <= 0:
                continue
            target = per_rung * w
            cands = band[band["categoria"] == cat]
            if cands.empty:
                warnings.append(f"Gradino {i + 1} ({fascia}y) · {cat}: nessun bond disponibile.")
                continue
            best = cands.loc[cands[yield_col].idxmax()]
            price = float(best["prezzo"])
            cost_per_lot = params.lot_nominal * price / 100.0
            if cost_per_lot <= 0:
                continue
            n_lots = int(target // cost_per_lot)
            if n_lots < 1:
                warnings.append(
                    f"Gradino {i + 1} ({fascia}y) · {cat}: importo {target:,.0f}€ "
                    f"< 1 lotto ({cost_per_lot:,.0f}€) per {best['isin']}."
                )
                continue
            importo = n_lots * cost_per_lot
            rows.append({
                "gradino": i + 1,
                "fascia_anni": fascia,
                "isin": best["isin"],
                "descrizione": best.get("descrizione"),
                "categoria": cat,
                "paese": best.get("paese"),
                "scadenza": best.get("scadenza"),
                "cedola_pct": best.get("cedola_pct"),
                "ytm_lordo": best.get("ytm_lordo"),
                "ytm_netto": best.get("ytm_netto"),
                "prezzo": price,
                "importo_eur": round(importo, 2),
                "n_titoli_nominale": n_lots * params.lot_nominal,
            })

    table = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    return LadderResult(
        table=table,
        warnings=warnings,
        weighted_ytm_gross=_weighted_avg(table, "ytm_lordo"),
        weighted_ytm_net=_weighted_avg(table, "ytm_netto"),
        capital_allocated=float(table["importo_eur"].sum()) if not table.empty else 0.0,
        n_bonds=int(len(table)),
    )
