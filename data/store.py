"""Persistenza su parquet, idempotente. Sostituisce lo strato SQLite.

File gestiti (vedi config):
  - universe.parquet : un record per ISIN (catalogo, categoria dai filtri)
  - prices.parquet   : (isin, date) -> price, storico multi-data
  - scrape_log.txt   : log append-only delle operazioni

Idempotenza (come la logica ON CONFLICT ... COALESCE dell'originale SQLite):
un ISIN già presente NON viene ri-aggiunto; i campi "first-seen"
(categoria/tipologia_bi/paese/paese_da_isin_fallback/timestamp_aggiunta) sono
preservati dalla prima occorrenza, mentre i campi di catalogo
(descrizione/cedola_pct/scadenza/valuta/url_scheda) vengono aggiornati.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Iterable, Optional

import pandas as pd

import config

# Schema universe.parquet (campi dal prompt). Dtype pandas nullable.
UNIVERSE_SCHEMA = {
    "isin": "string",
    "descrizione": "string",
    "cedola_pct": "float64",
    "scadenza": "string",
    "valuta": "string",
    "categoria": "string",
    "tipologia_bi": "string",
    "paese": "string",
    "paese_da_isin_fallback": "boolean",
    "freq_cedola": "Int64",          # null finché non recuperata (non in v1)
    "data_godimento": "string",      # null finché non recuperata (non in v1)
    "url_scheda": "string",
    "mercato": "string",             # MOT | EuroTLX (mercato di prima vista)
    "timestamp_aggiunta": "string",
}
UNIVERSE_COLUMNS = list(UNIVERSE_SCHEMA.keys())

# Campi aggiornati ad ogni ri-scrape; il resto è "first-seen" (preservato).
_REFRESH_FIELDS = ["descrizione", "cedola_pct", "scadenza", "valuta", "url_scheda"]

PRICES_COLUMNS = ["isin", "date", "price"]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _empty_universe() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in UNIVERSE_SCHEMA.items()})


def _empty_prices() -> pd.DataFrame:
    return pd.DataFrame({
        "isin": pd.Series(dtype="string"),
        "date": pd.Series(dtype="string"),
        "price": pd.Series(dtype="float64"),
    })


def _coerce_universe(df: pd.DataFrame) -> pd.DataFrame:
    for col, dt in UNIVERSE_SCHEMA.items():
        if col not in df.columns:
            df[col] = pd.NA
        try:
            if dt == "boolean":
                df[col] = df[col].fillna(False).astype("boolean")
            else:
                df[col] = df[col].astype(dt)
        except (TypeError, ValueError):
            pass
    return df[UNIVERSE_COLUMNS]


def _atomic_write(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _rec_to_dict(rec) -> dict:
    if isinstance(rec, dict):
        return dict(rec)
    return dict(getattr(rec, "__dict__", {}))


# ──────────────────────────────────────────────────────────────────────────────
# Universe
# ──────────────────────────────────────────────────────────────────────────────
def load_universe(path: Optional[str] = None) -> pd.DataFrame:
    path = path or config.UNIVERSE_PARQUET
    if not os.path.exists(path):
        return _empty_universe()
    try:
        return pd.read_parquet(path)
    except Exception:
        return _empty_universe()


def upsert_universe(records: Iterable, path: Optional[str] = None) -> dict:
    """Inserisce/aggiorna record nell'universo in modo idempotente.

    `records`: iterabile di dict (o dataclass) con le chiavi del catalogo
    (isin, descrizione, cedola_pct, scadenza, valuta, categoria, tipologia_bi,
    paese, paese_da_isin_fallback, url_scheda). Chiavi extra (es. ultimo_price)
    sono ignorate.

    Ritorna {'added', 'skipped', 'fallback_isin'}.
    """
    path = path or config.UNIVERSE_PARQUET
    old = _coerce_universe(load_universe(path))
    old_isins = set(old["isin"].dropna().tolist())
    now = datetime.now().isoformat(timespec="seconds")

    rows, seen_new = [], set()
    added = skipped = fallback = 0
    for rec in records:
        d = _rec_to_dict(rec)
        isin = d.get("isin")
        if not isin:
            continue
        if isin in old_isins or isin in seen_new:
            skipped += 1
        else:
            added += 1
            if d.get("paese_da_isin_fallback"):
                fallback += 1
        seen_new.add(isin)
        d.setdefault("timestamp_aggiunta", now)
        rows.append(d)

    if not rows:
        return {"added": 0, "skipped": skipped, "fallback_isin": 0}

    new = _coerce_universe(pd.DataFrame(rows, columns=UNIVERSE_COLUMNS)
                           .drop_duplicates(subset="isin", keep="first"))

    old_i = old.set_index("isin")
    new_i = new.set_index("isin")
    combined = old_i.copy()
    overlap = new_i.index.intersection(old_i.index)
    if len(overlap):
        for col in _REFRESH_FIELDS:
            combined.loc[overlap, col] = new_i.loc[overlap, col]
    brand_new = new_i.index.difference(old_i.index)
    if len(brand_new):
        combined = pd.concat([combined, new_i.loc[brand_new]])

    out = _coerce_universe(combined.reset_index())
    _atomic_write(out, path)
    return {"added": added, "skipped": skipped, "fallback_isin": fallback}


# ──────────────────────────────────────────────────────────────────────────────
# Prices
# ──────────────────────────────────────────────────────────────────────────────
def load_prices(path: Optional[str] = None) -> pd.DataFrame:
    path = path or config.PRICES_PARQUET
    if not os.path.exists(path):
        return _empty_prices()
    try:
        return pd.read_parquet(path)
    except Exception:
        return _empty_prices()


def save_prices(price_map: dict, on_date: Optional[str] = None,
                path: Optional[str] = None) -> int:
    """Aggiunge una riga di prezzo per ISIN alla data `on_date` (default oggi).
    Storico multi-data: dedup su (isin, date) tenendo l'ultimo. Ritorna n. righe
    nuove scritte."""
    path = path or config.PRICES_PARQUET
    if not price_map:
        return 0
    on_date = on_date or date.today().isoformat()
    new = pd.DataFrame(
        [{"isin": k, "date": on_date, "price": float(v)}
         for k, v in price_map.items() if v is not None],
        columns=PRICES_COLUMNS,
    )
    if new.empty:
        return 0
    old = load_prices(path)
    combined = pd.concat([old, new], ignore_index=True)
    combined = combined.drop_duplicates(subset=["isin", "date"], keep="last")
    _atomic_write(combined, path)
    return int(len(new))


def latest_prices(path: Optional[str] = None) -> pd.DataFrame:
    """Prezzo più recente per ISIN (colonne: isin, price, date)."""
    p = load_prices(path)
    if p.empty:
        return p
    idx = p.groupby("isin")["date"].idxmax()
    return p.loc[idx].reset_index(drop=True)


def last_price_timestamp(path: Optional[str] = None) -> Optional[str]:
    p = load_prices(path)
    if p.empty:
        return None
    return str(p["date"].max())


def universe_with_latest_price(
    universe_path: Optional[str] = None, prices_path: Optional[str] = None
) -> pd.DataFrame:
    """Universo + colonna `prezzo` (ultimo) e `prezzo_data`."""
    u = load_universe(universe_path)
    if u.empty:
        u = u.copy()
        u["prezzo"] = pd.Series(dtype="float64")
        u["prezzo_data"] = pd.Series(dtype="string")
        return u
    lp = latest_prices(prices_path)
    if lp.empty:
        u = u.copy()
        u["prezzo"] = pd.NA
        u["prezzo_data"] = pd.NA
        return u
    lp = lp.rename(columns={"price": "prezzo", "date": "prezzo_data"})
    return u.merge(lp[["isin", "prezzo", "prezzo_data"]], on="isin", how="left")


# ──────────────────────────────────────────────────────────────────────────────
# Log
# ──────────────────────────────────────────────────────────────────────────────
def log_line(op: str, *fields: str, ts: Optional[str] = None) -> str:
    ts = ts or datetime.now().isoformat(sep=" ", timespec="seconds")
    parts = [ts, op.ljust(5)] + [str(f) for f in fields]
    return " | ".join(parts)


def append_log(line: str, path: Optional[str] = None) -> None:
    path = path or config.SCRAPE_LOG
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
