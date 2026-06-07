"""Bond Ladder — app Streamlit standalone.

Tre tab:
  1) Overview      — scatter YTM vs scadenza + tabella bond eligible
  2) Aggiorna Dati — scarica l'universo da Borsa Italiana e aggiorna i prezzi
  3) Bond Ladder   — costruzione del ladder parametrico

Avvio:  streamlit run app.py
Il rendimento (YTM) è SEMPRE calcolato dai prezzi correnti, mai scaricato.
"""
from __future__ import annotations

import itertools
import os
from dataclasses import asdict

import streamlit as st

import config
from data import store
from finance.enrich import add_yield_columns
from ladder.builder import build_ladder
from scraper.eurotlx import build_eurotlx_profiles, scrape_eurotlx, update_prices_eurotlx
from scraper.price_updater import update_prices
from scraper.search import build_profiles, scrape_universe
from ui.charts import CATEGORY_LABELS, scatter_yield_vs_maturity, stacked_bar_ladder
from ui.filters import apply_filters
from ui.sidebar import global_controls, ladder_form

CATEGORIE_ALL = ["gov_ita", "corp_ita", "gov_eur", "corp_eur"]

st.set_page_config(page_title="Bond Ladder", page_icon="🪜", layout="wide")


def _data_sig() -> str:
    def mt(p: str) -> float:
        return os.path.getmtime(p) if os.path.exists(p) else 0.0
    return f"{mt(config.UNIVERSE_PARQUET):.0f}-{mt(config.PRICES_PARQUET):.0f}"


@st.cache_data(show_spinner=False)
def get_enriched(sig: str, freq: int, daycount: str, apply_bollo: bool):
    df = store.universe_with_latest_price()
    if df.empty:
        return df
    return add_yield_columns(df, freq=freq, convention=daycount, apply_bollo=apply_bollo)


def _table_columns(df):
    cols = ["isin", "descrizione", "categoria", "mercato", "paese", "valuta", "scadenza",
            "cedola_pct", "prezzo", "anni_scadenza", "ytm_lordo", "ytm_netto"]
    return [c for c in cols if c in df.columns]


# ── Sidebar globale ─────────────────────────────────────────────────────────
settings = global_controls()
sig = _data_sig()

st.title("🪜 Bond Ladder")
st.caption("Universo plain vanilla da Borsa Italiana · categoria dai filtri · "
           "YTM lordo/netto calcolato (fisco IT 2026)")

tab_overview, tab_data, tab_ladder = st.tabs(["📊 Overview", "🔄 Aggiorna Dati", "🪜 Bond Ladder"])


# ── Tab 1 — Overview ────────────────────────────────────────────────────────
with tab_overview:
    enr = get_enriched(sig, settings["freq"], settings["daycount"], settings["apply_bollo"])
    ts = store.last_price_timestamp()
    c1, c2 = st.columns([3, 1])
    c1.caption(f"Prezzi aggiornati al: **{ts or '—'}**")
    if enr.empty:
        st.info("Universo vuoto. Vai nel tab **Aggiorna Dati** e scarica l'universo da Borsa Italiana.")
    else:
        c2.metric("Bond in universo", len(enr))
        ycol = "ytm_netto" if settings["use_net"] else "ytm_lordo"
        ylabel = "netto" if settings["use_net"] else "lordo"

        # Pannello filtri condiviso: lo stesso `view` alimenta grafico e tabella,
        # così si aggiornano insieme a ogni cambio di filtro.
        with st.expander("🔎 Filtri", expanded=True):
            f1, f2, f3 = st.columns(3)
            cats = f1.multiselect("Categorie", options=CATEGORIE_ALL, default=CATEGORIE_ALL,
                                  format_func=lambda c: CATEGORY_LABELS.get(c, c), key="ov_cats")
            val_opts = sorted(enr["valuta"].dropna().astype(str).unique().tolist())
            vals = f2.multiselect("Valute", options=val_opts, default=val_opts, key="ov_valute")
            mkt_opts = (sorted(enr["mercato"].dropna().astype(str).unique().tolist())
                        if "mercato" in enr.columns else [])
            mkts = f3.multiselect("Mercati", options=mkt_opts, default=mkt_opts, key="ov_mercati")

            anni_arg = ytm_arg = None
            s1, s2 = st.columns(2)
            anni_vals = enr["anni_scadenza"].dropna()
            if not anni_vals.empty and float(anni_vals.max()) > float(anni_vals.min()):
                amin, amax = float(anni_vals.min()), float(anni_vals.max())
                a_lo, a_hi = s1.slider("Anni alla scadenza", amin, amax, (amin, amax), step=0.25)
                if a_lo > amin or a_hi < amax:
                    anni_arg = (a_lo, a_hi)
            ytm_series = enr[ycol].dropna()
            if not ytm_series.empty and float(ytm_series.max()) > float(ytm_series.min()):
                ymin, ymax = float(ytm_series.min()), float(ytm_series.max())
                y_lo, y_hi = s2.slider(f"YTM {ylabel} (%)", ymin, ymax, (ymin, ymax), step=0.05)
                if y_lo > ymin or y_hi < ymax:
                    ytm_arg = (y_lo, y_hi)

            query = st.text_input("Cerca (ISIN o descrizione)", "")

        view = apply_filters(
            enr, categorie=cats or None, valute=vals or None, mercati=mkts or None,
            anni_range=anni_arg, ytm_range=ytm_arg, ycol=ycol, query=query,
        )
        st.caption(f"Mostrati **{len(view)}** bond su {len(enr)}")
        st.plotly_chart(
            scatter_yield_vs_maturity(view, use_net=settings["use_net"]),
            width="stretch",
        )
        st.dataframe(
            view[_table_columns(view)].sort_values(ycol, ascending=False, na_position="last"),
            width="stretch", hide_index=True,
        )


# ── Tab 2 — Aggiorna Dati ───────────────────────────────────────────────────
with tab_data:
    universe = store.load_universe()
    st.subheader("A. Aggiorna universo")
    cc1, _cc2, _cc3 = st.columns(3)
    cc1.metric("ISIN attualmente in universo", len(universe))
    markets = st.multiselect(
        "Mercati", options=list(config.MERCATI), default=list(config.MERCATI), key="data_markets",
        help="MOT via Selenium (filtri server-side). EuroTLX via richiesta diretta "
             "(più veloce); eligibilità plain-vanilla/fissa dal nome.",
    )
    valute = st.multiselect("Valute", options=list(config.VALUTE), default=list(config.VALUTE),
                            key="data_valute")
    st.caption("Include sempre gli zero-coupon (BOT, CTZ, …). Paese dedotto dal prefisso ISIN. "
               "Salvataggio incrementale su parquet durante lo scraping (resiste ai crash).")

    if st.button("⬇️ Scarica universo da BI", type="primary"):
        vals = tuple(valute) if valute else config.VALUTE
        mot_profiles = build_profiles(valute=vals, include_zero_coupon=True) if "MOT" in markets else []
        tlx_profiles = (build_eurotlx_profiles(valute=vals, include_zero_coupon=True)
                        if "EuroTLX" in markets else [])
        total = len(mot_profiles) + len(tlx_profiles)
        prog = st.progress(0.0, text=f"0/{total}")
        log_area = st.empty()
        state = {"done": 0, "logs": []}

        def cb(p):
            if p.done or p.error:
                state["done"] += 1
                tag = "ERR" if p.error else "OK "
                state["logs"].append(
                    f"{tag} · {p.categoria} · {p.profile_label} · {p.rows_so_far} righe"
                    + (f" · {p.error}" if p.error else "")
                )
                log_area.code("\n".join(state["logs"][-18:]))
            frac = min(state["done"] / total, 1.0) if total else 1.0
            prog.progress(frac, text=f"{state['done']}/{total} · {p.profile_label} "
                                     f"(pag {p.page}, {p.rows_so_far} righe)")

        # Salvataggio INCREMENTALE: flush su parquet ogni FLUSH_EVERY record, così un
        # crash a metà non perde tutto e il progresso è visibile su disco. MOT prima
        # nella catena → per un ISIN su entrambi i mercati vince MOT (upsert keep-first).
        FLUSH_EVERY = 150
        tot = {"records": 0, "added": 0, "skipped": 0, "fallback": 0, "prezzi": 0}
        buf, price_buf = [], {}

        def flush():
            if not buf:
                return
            r = store.upsert_universe([asdict(x) for x in buf])
            tot["added"] += r["added"]
            tot["skipped"] += r["skipped"]
            tot["fallback"] += r["fallback_isin"]
            if price_buf:
                tot["prezzi"] += store.save_prices(dict(price_buf))
                price_buf.clear()
            buf.clear()

        gens = []
        if mot_profiles:
            gens.append(scrape_universe(mot_profiles, headless=True, progress_cb=cb))
        if tlx_profiles:
            gens.append(scrape_eurotlx(tlx_profiles, include_zero_coupon=True, progress_cb=cb))
        try:
            with st.spinner("Scraping in corso (salvataggio incrementale)…"):
                for rec in itertools.chain(*gens):
                    buf.append(rec)
                    tot["records"] += 1
                    if rec.ultimo_price is not None:
                        price_buf[rec.isin] = rec.ultimo_price
                    if len(buf) >= FLUSH_EVERY:
                        flush()
                flush()
            store.append_log(store.log_line(
                "RUN", f"mercati={'+'.join(markets) or '-'}", f"record={tot['records']}",
                f"added={tot['added']}", f"skip={tot['skipped']}", f"prezzi={tot['prezzi']}",
            ))
            get_enriched.clear()
            st.success(
                f"Fatto ({'+'.join(markets) or 'nessun mercato'}). Record: {tot['records']} · "
                f"nuovi: {tot['added']} · già presenti: {tot['skipped']} · "
                f"paese da ISIN: {tot['fallback']} · prezzi salvati: {tot['prezzi']}."
            )
        except Exception as exc:  # noqa: BLE001
            flush()  # salva quanto raccolto prima dell'errore
            get_enriched.clear()
            store.append_log(store.log_line("RUN_ERR", f"record_parziali={tot['records']}", str(exc)[:80]))
            st.error(f"Interrotto da un errore — dati parziali salvati ({tot['records']} record): {exc}")

    st.divider()
    st.subheader("B. Aggiorna prezzi")
    st.caption(f"Ultimo aggiornamento prezzi: **{store.last_price_timestamp() or '—'}**")
    if st.button("🔄 Aggiorna prezzi"):
        known = store.load_universe()
        if known.empty:
            st.warning("Universo vuoto: scarica prima l'universo.")
        else:
            vals_known = tuple(known["valuta"].dropna().astype(str).unique()) or config.VALUTE
            has_mkt = "mercato" in known.columns
            has_tlx = has_mkt and (known["mercato"] == "EuroTLX").any()
            has_mot = (not has_mkt) or (known["mercato"] != "EuroTLX").any()
            total2 = (len(build_profiles(valute=vals_known, include_zero_coupon=True)) if has_mot else 0) \
                + (len(build_eurotlx_profiles(valute=vals_known, include_zero_coupon=True)) if has_tlx else 0)
            prog2 = st.progress(0.0, text="Avvio…")
            log2 = st.empty()
            st2 = {"done": 0, "logs": []}

            def cb2(p):
                if p.done or p.error:
                    st2["done"] += 1
                    st2["logs"].append(f"{'ERR' if p.error else 'OK '} · {p.profile_label} · {p.rows_so_far}")
                    log2.code("\n".join(st2["logs"][-15:]))
                frac = min(st2["done"] / total2, 1.0) if total2 else 1.0
                prog2.progress(frac, text=f"{st2['done']}/{total2} · {p.profile_label}")

            try:
                n = 0
                if has_mot:
                    mot_known = known[known["mercato"] != "EuroTLX"] if has_mkt else known
                    with st.spinner("Prezzi MOT (Selenium)…"):
                        n += store.save_prices(update_prices(mot_known, headless=True,
                                                             include_zero_coupon=True, progress_cb=cb2))
                if has_tlx:
                    tlx_isins = set(known[known["mercato"] == "EuroTLX"]["isin"])
                    with st.spinner("Prezzi EuroTLX…"):
                        n += store.save_prices(update_prices_eurotlx(tlx_isins, valute=vals_known,
                                                                     include_zero_coupon=True, progress_cb=cb2))
                store.append_log(store.log_line("PRICES", f"aggiornati={n}"))
                get_enriched.clear()
                st.success(f"Prezzi aggiornati: {n}.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Errore durante l'aggiornamento prezzi: {exc}")


# ── Tab 3 — Bond Ladder ─────────────────────────────────────────────────────
with tab_ladder:
    enr = get_enriched(sig, settings["freq"], settings["daycount"], settings["apply_bollo"])
    if enr.empty:
        st.info("Universo vuoto. Scarica prima l'universo nel tab **Aggiorna Dati**.")
    else:
        params, tot = ladder_form(settings)
        if st.button("🪜 Costruisci Ladder", type="primary", disabled=(params is None)):
            res = build_ladder(enr, params)
            if res.table.empty:
                st.warning("Nessun bond selezionato. Rivedi durata massima/allocazioni.")
            else:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Capitale allocato", f"{res.capital_allocated:,.0f} €")
                m2.metric("YTM medio lordo", f"{res.weighted_ytm_gross:.2f} %")
                m3.metric("YTM medio netto", f"{res.weighted_ytm_net:.2f} %")
                m4.metric("Bond usati", res.n_bonds)
                st.plotly_chart(stacked_bar_ladder(res.table), width="stretch")
                st.dataframe(res.table, width="stretch", hide_index=True)
            for w in res.warnings:
                st.warning(w)
