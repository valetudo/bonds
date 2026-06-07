"""OPZIONALE / FUTURO — recupero dei due dati presenti solo nella scheda ISIN:
frequenza cedolare e data ultimo godimento (per il rateo esatto).

NON usato nella v1: la modalità "Bilanciata" assume la frequenza (configurabile)
e stima il rateo ricostruendo le date cedola dalla scadenza, quindi non serve
visitare le schede. Questo modulo è uno stub con la firma definita, da
implementare se in futuro si vuole la massima precisione.

Vincolo (quando implementato): pausa random obbligatoria di
random.uniform(*config.DETAIL_DELAY) tra una scheda e l'altra, mai sotto 2 s.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional


def enrich_details(
    isins: Iterable[str],
    universe=None,
    *,
    headless: bool = True,
    cancel_flag: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable] = None,
) -> Dict[str, dict]:
    """Ritornerebbe {isin: {"freq_cedola": int|None, "data_godimento": str|None}}.

    Flusso idempotente (salta ISIN già completi), pausa anti-ban obbligatoria,
    estrazione da url_scheda. Non implementato in v1.
    """
    raise NotImplementedError(
        "scraper/detail.py non è attivo nella v1 (modalità 'Bilanciata': "
        "frequenza assunta + rateo stimato, nessuna visita alle schede)."
    )
