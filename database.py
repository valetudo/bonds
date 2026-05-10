"""SQLite layer for the bonds project.

Single-file local database. Schema is created on first connect.

Tables
------
bonds          : one row per ISIN, with latest catalog metadata
bond_prices    : (isin, date) -> price; multiple historical points per bond
scrape_runs    : audit log of each sync attempt
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator, List, Optional


STALE_THRESHOLD_DAYS = 14  # bonds not seen in this many days become inactive


_DB_FILENAME = "bonds.db"


class Database:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else Path(__file__).resolve().parent / _DB_FILENAME
        self._ensure_schema()

    # ------------------------------------------------------------------ #
    # Connection helpers
    # ------------------------------------------------------------------ #
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            # Step 1: tables (no tipologia index yet — column may not exist
            # in pre-migration databases).
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bonds (
                    isin           TEXT PRIMARY KEY,
                    name           TEXT NOT NULL,
                    coupon         REAL,
                    maturity_date  TEXT,
                    currency       TEXT DEFAULT 'EUR',
                    category       TEXT,
                    tipologia      TEXT,
                    nation         TEXT,
                    issuer_type    TEXT,
                    geo_area       TEXT,
                    first_seen     TEXT,
                    last_seen      TEXT,
                    is_active      INTEGER DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_bonds_category ON bonds(category);

                CREATE TABLE IF NOT EXISTS bond_prices (
                    isin   TEXT NOT NULL,
                    date   TEXT NOT NULL,
                    price  REAL NOT NULL,
                    PRIMARY KEY (isin, date),
                    FOREIGN KEY (isin) REFERENCES bonds(isin) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_prices_date ON bond_prices(date);

                CREATE TABLE IF NOT EXISTS scrape_runs (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at     TEXT NOT NULL,
                    finished_at    TEXT,
                    profile        TEXT,
                    rows_scraped   INTEGER DEFAULT 0,
                    status         TEXT,
                    error_message  TEXT
                );
                """
            )
            # Step 2: idempotent ALTER TABLE for existing pre-migration DBs.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(bonds)").fetchall()}
            if "tipologia" not in cols:
                conn.execute("ALTER TABLE bonds ADD COLUMN tipologia TEXT")
            if "nation" not in cols:
                conn.execute("ALTER TABLE bonds ADD COLUMN nation TEXT")
            if "is_active" not in cols:
                conn.execute("ALTER TABLE bonds ADD COLUMN is_active INTEGER DEFAULT 1")
                # Treat existing rows as active until proven stale by
                # mark_stale_inactive() at the end of the next scrape.
                conn.execute("UPDATE bonds SET is_active = 1 WHERE is_active IS NULL")
            # Step 3: indexes — guaranteed safe now that the columns exist.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bonds_tipologia ON bonds(tipologia)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bonds_nation ON bonds(nation)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bonds_is_active ON bonds(is_active)")

    # ------------------------------------------------------------------ #
    # Bonds
    # ------------------------------------------------------------------ #
    def upsert_bond(
        self,
        isin: str,
        name: str,
        coupon: Optional[float],
        maturity_date: Optional[str],
        currency: str,
        category: str,
        issuer_type: Optional[str],
        geo_area: Optional[str],
        tipologia: Optional[str] = None,
        nation: Optional[str] = None,
        seen_at: Optional[str] = None,
    ) -> None:
        seen_at = seen_at or datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bonds
                  (isin, name, coupon, maturity_date, currency, category,
                   tipologia, nation, issuer_type, geo_area, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(isin) DO UPDATE SET
                  name=excluded.name,
                  coupon=COALESCE(excluded.coupon, bonds.coupon),
                  maturity_date=COALESCE(excluded.maturity_date, bonds.maturity_date),
                  currency=excluded.currency,
                  -- Preserve first-seen category, tipologia, nation: a bond
                  -- that appears in multiple profile filters keeps the first.
                  category=COALESCE(bonds.category, excluded.category),
                  tipologia=COALESCE(bonds.tipologia, excluded.tipologia),
                  nation=COALESCE(bonds.nation, excluded.nation),
                  issuer_type=COALESCE(excluded.issuer_type, bonds.issuer_type),
                  geo_area=COALESCE(excluded.geo_area, bonds.geo_area),
                  -- Re-seeing an ISIN reactivates it: a bond previously
                  -- marked stale by mark_stale_inactive() but listed again
                  -- by BI on a later scrape comes back into the screener.
                  is_active=1,
                  last_seen=excluded.last_seen
                """,
                (isin, name, coupon, maturity_date, currency, category,
                 tipologia, nation, issuer_type, geo_area, seen_at, seen_at),
            )

    def upsert_price(self, isin: str, date: str, price: float) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bond_prices (isin, date, price)
                VALUES (?, ?, ?)
                ON CONFLICT(isin, date) DO UPDATE SET price=excluded.price
                """,
                (isin, date, price),
            )

    def list_bonds_with_latest_price(
        self, include_inactive: bool = False
    ) -> List[dict]:
        """Return every bond with its most recent price (NULL if none).

        By default excludes rows soft-purged by mark_stale_inactive() —
        bonds whose `last_seen` fell below the staleness threshold and
        haven't been re-scraped since. Pass include_inactive=True for
        debug/audit views that want the full history."""
        sql = """
            SELECT b.isin, b.name, b.coupon, b.maturity_date, b.currency,
                   b.category, b.tipologia, b.nation, b.issuer_type, b.geo_area,
                   b.first_seen, b.last_seen, b.is_active,
                   p.price AS latest_price, p.date AS latest_price_date
            FROM bonds b
            LEFT JOIN (
                SELECT bp.isin, bp.price, bp.date
                FROM bond_prices bp
                INNER JOIN (
                    SELECT isin, MAX(date) AS max_date
                    FROM bond_prices
                    GROUP BY isin
                ) latest ON latest.isin = bp.isin AND latest.max_date = bp.date
            ) p ON p.isin = b.isin
        """
        if not include_inactive:
            sql += "\nWHERE b.is_active = 1"
        sql += "\nORDER BY b.isin"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def mark_stale_inactive(self, days: int = STALE_THRESHOLD_DAYS) -> int:
        """Soft-purge bonds whose `last_seen` is older than `days` days.

        Returns the number of rows newly marked inactive. The records and
        their historical prices are kept in DB; they're just hidden from
        the default screener/chart queries. A subsequent scrape that
        re-encounters the ISIN will reactivate it (see upsert_bond)."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE bonds SET is_active = 0 "
                "WHERE last_seen < ? AND is_active = 1",
                (cutoff,),
            )
            return cur.rowcount or 0

    def count_bonds(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM bonds").fetchone()
            return int(row["n"]) if row else 0

    def count_with_price(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT isin) AS n FROM bond_prices"
            ).fetchone()
            return int(row["n"]) if row else 0

    def delete_bond(self, isin: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM bonds WHERE isin = ?", (isin,))

    def reset_categories(self) -> None:
        """Set every bond's `category`, `tipologia`, and `nation` to NULL.

        Call before a full multi-profile scrape so the upsert COALESCE
        logic populates the correct values on first encounter without
        being shadowed by a previous run.
        """
        with self.connect() as conn:
            conn.execute(
                "UPDATE bonds SET category = NULL, tipologia = NULL, nation = NULL"
            )

    # ------------------------------------------------------------------ #
    # Scrape audit log
    # ------------------------------------------------------------------ #
    def start_scrape_run(self, profile: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scrape_runs (started_at, profile, status)
                VALUES (?, ?, 'running')
                """,
                (datetime.now().isoformat(timespec="seconds"), profile),
            )
            return int(cur.lastrowid)

    def finish_scrape_run(
        self,
        run_id: int,
        rows_scraped: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE scrape_runs
                SET finished_at = ?, rows_scraped = ?, status = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    rows_scraped,
                    status,
                    error_message,
                    run_id,
                ),
            )

    def last_scrape_run(self) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def list_scrape_runs(self, limit: int = 20) -> List[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
