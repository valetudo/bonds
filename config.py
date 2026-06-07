"""Single source of truth per costanti NON finanziarie del progetto.

Tutto ciò che il prompt chiede di "documentare in commenti" (URL, id dei
<select>, value reali dei filtri, lista paesi) vive qui, niente stringhe magiche
sparse nel codice. I valori dei filtri sono stati verificati sul DOM della
pagina di ricerca avanzata di Borsa Italiana.
"""
from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────────────
# Borsa Italiana — pagina di ricerca avanzata obbligazioni
# ──────────────────────────────────────────────────────────────────────────────
ADVANCED_SEARCH_URL = (
    "https://www.borsaitaliana.it/borsa/obbligazioni/ricerca-avanzata.html"
)

# Id DOM dei <select> del form (dal dump diagnostico del DOM live).
#   structures    -> "Struttura"
#   typologies    -> "Tipologia"
#   types         -> "Tipo Cedola"
#   subordination -> "Subordinazione"
#   countries     -> "Paese"
#   callable      -> "Rimborso Anticipato"  (DA NON USARE, vedi sotto)
SEL_STRUTTURA = "structures"
SEL_TIPOLOGIA = "typologies"
SEL_TIPO_CEDOLA = "types"
SEL_SUBORDINAZIONE = "subordination"
SEL_PAESE = "countries"
# Id del filtro Valuta: NON confermato nel codice esistente (lo screener
# deduceva la valuta dal nome). Va verificato col probe (vedi scraper/probe.py).
# Best-effort: se il <select> non esiste/non si applica, lo scraper continua e
# la valuta del record resta quella del profilo richiesto.
SEL_VALUTA = "currencies"

# Filtro "Rimborso Anticipato = No" NON applicabile server-side: il backend BI
# tratta NULL != "No" e quindi azzera tutti i governativi (campo non valorizzato
# per loro). I callable si scartano lato client dal nome (is_callable_from_name).
SEL_CALLABLE_NON_USARE = "callable"

# Value/label dei <option> (match sul TESTO visibile, case-insensitive).
OPT_PLAIN_VANILLA = "Plain Vanilla"
OPT_CEDOLA_FISSA = "Titolo Con Cedole Tf"
OPT_ZERO_COUPON = "Zero Coupon"
OPT_NO = "No"

# ──────────────────────────────────────────────────────────────────────────────
# Tipologie BI → categoria del progetto (categoria SEMPRE dai filtri)
# ──────────────────────────────────────────────────────────────────────────────
TIPOLOGIE_GOV_ITA = ("Titoli Di Stato Italiani", "Eurobonds Republic Of Italy")
TIPOLOGIE_GOV_EUR = ("Titoli Di Stato Esteri",)
TIPOLOGIE_CORP = ("Banche", "Corporate", "Secured")

# Le 4 categorie target.
CATEGORIE = ("gov_ita", "gov_eur", "corp_ita", "corp_eur")

# Valute ammesse (due passate, o multiselect se il form lo consente).
VALUTE = ("EUR", "USD")

# Paesi != Italia rilevanti (perimetro EUR/USD). Il testo deve combaciare con il
# label del dropdown "Paese" di BI (lo scraper matcha sul testo dell'option).
PAESE_ITALIA = "Italia"
PAESI_NON_ITALIA = (
    # Eurozona core
    "Francia", "Germania", "Spagna", "Austria", "Belgio", "Olanda",
    "Portogallo", "Finlandia", "Irlanda", "Lussemburgo",
    # Eurozona periferia
    "Grecia", "Cipro", "Slovenia", "Estonia", "Lettonia", "Lituania",
    # CEE
    "Polonia", "Ungheria", "Romania", "Bulgaria", "Croazia",
    # Europa non-euro
    "Gran Bretagna", "Svizzera", "Norvegia", "Svezia",
    # Extra-Europa
    "Stati Uniti", "Canada",
)

# ──────────────────────────────────────────────────────────────────────────────
# Anti-ban / timeout Selenium
# ──────────────────────────────────────────────────────────────────────────────
PAGE_DELAY = (1.5, 4.0)      # pausa random tra pagine della lista
DETAIL_DELAY = (2.5, 6.0)    # pausa random tra schede (modulo detail, non in v1)
PAGE_SIZE_OVERRIDE = 200
MAX_PAGES_PER_PROFILE = 50
WAIT_FORM_SECONDS = 15
WAIT_RESULTS_SECONDS = 20

# ──────────────────────────────────────────────────────────────────────────────
# Default finanza (rendimento "Bilanciata")
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_COUPON_FREQ = 1        # frequenza cedola assunta (1 = annuale, 2 = semestrale)
DEFAULT_DAYCOUNT = "ACT/ACT"   # convenzione day-count dichiarata
REDEMPTION = 100.0             # valore di rimborso alla pari (plain vanilla)
LOTTO_NOMINALE = 1000.0        # taglio minimo nominale retail per il ladder

# ──────────────────────────────────────────────────────────────────────────────
# Percorsi dati (parquet)
# ──────────────────────────────────────────────────────────────────────────────
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
UNIVERSE_PARQUET = os.path.join(DATA_DIR, "universe.parquet")
PRICES_PARQUET = os.path.join(DATA_DIR, "prices.parquet")
SCRAPE_LOG = os.path.join(DATA_DIR, "scrape_log.txt")

# ──────────────────────────────────────────────────────────────────────────────
# EuroTLX (seconda fonte) — ricerca avanzata dedicata, endpoint risultati diretto
# ──────────────────────────────────────────────────────────────────────────────
# A differenza di MOT, EuroTLX classifica per CATEGORIA strumento (44 voci) e non
# ha filtri Struttura/Cedola/Subordinazione: l'eligibilità (plain vanilla, fissa,
# no callable) si applica lato client dal nome. I risultati arrivano da un GET
# diretto (niente Selenium): risultati.html?category=<CODE>&currency=<CCY>&size&page
EUROTLX_RESULTS_URL = (
    "https://www.borsaitaliana.it/borsa/obbligazioni/eurotlx/ricerca-avanzata/risultati.html"
)
EUROTLX_PAGE_SIZE = 200          # il server cappa ~100/pagina; si pagina comunque
EUROTLX_PAGE_DELAY = (1.0, 2.5)  # pausa tra pagine (GET leggero, ma educati)

MERCATI = ("MOT", "EuroTLX")

# Codice categoria EuroTLX -> bucket del progetto. Categoria gov/corp dal filtro;
# ita/estero dei corp dal prefisso ISIN. Supranational/agency -> gov_eur (white-list
# 12,5%). Territoriali -> corp (prudenziale sul fisco).
EUROTLX_CATEGORY_BUCKET = {
    # gov_ita
    "BTP": "gov_ita", "BTP_FUTURA": "gov_ita", "BTP_ITALIA": "gov_ita",
    "OTHER_ITALIAN_GOVIES": "gov_ita",
    # gov_eur (sovrani esteri + sovranazionali white-list)
    "OTHER_EU_GOVIES": "gov_eur", "BOBL": "gov_eur", "BTAN": "gov_eur",
    "BUND": "gov_eur", "OAT": "gov_eur", "SCHATZE": "gov_eur", "SOVEREIGN": "gov_eur",
    "T-BONDS": "gov_eur", "T-NOTE": "gov_eur", "BELGIAN_GOVERNMENT_BONDS": "gov_eur",
    "DUTCH_GOVERNMENT_BONDS": "gov_eur", "PORTUGUESE_GOVERNMENT_BONDS": "gov_eur",
    "NORWEGIAN_GOVERNMENT_BONDS": "gov_eur", "EMERGING_MARKET_GOVIES": "gov_eur",
    "EXTRA_EU_GOVIES": "gov_eur", "SUPRANATIONAL_AGENCY": "gov_eur",
    # corp (ita/estero risolto dall'ISIN)
    "CORPORATE_BONDS": "corp", "FINANCIAL_BONDS": "corp", "COVERED_BONDS": "corp",
    "INFRASTRUCTURE_BONDS": "corp", "NOTES": "corp", "OTHER_DEBT_INSTRUMENTS": "corp",
    "TERRITORIAL_BONDS": "corp",
}
# Categorie zero-coupon/bills: incluse solo se include_zero_coupon=True
EUROTLX_ZERO_BUCKET = {
    "BOT": "gov_ita", "CTZ": "gov_ita", "BTF": "gov_eur",
    "NORWEGIAN_GOVERNMENT_BILLS": "gov_eur",
}
# Categorie escluse per disegno (ABS/cartolarizzazioni, inflation-linked, floater
# CCT, convertibili, cambiali/crediti, tender). Documentate per chiarezza.
EUROTLX_EXCLUDED = {
    "ABS", "ABS_STS", "SYNTHETIC_ABS_STS", "SYNTHETIC_ABS", "COMMERCIAL_PAPERS",
    "CREDITS", "BOND_TENDER_OFFER", "CONVERTIBLE_BONDS", "CCT",
    "BTP_INFLATION", "OAT_INFLATION", "TIPS",
}
