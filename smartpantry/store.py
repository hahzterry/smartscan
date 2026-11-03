"""Shared store: relational tables for purchases/prices, JSON for the rest.

SQLite via the standard library. Receipt images live on disk next to the
database (object storage in production); only their paths are stored.
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional

from .schemas import (
    Plan,
    PriceObservation,
    Product,
    Purchase,
    RunLog,
    WatchlistItem,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  product_id TEXT PRIMARY KEY,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchases (
  purchase_id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL,
  date TEXT NOT NULL,
  store TEXT NOT NULL,
  quantity REAL NOT NULL,
  price_paid REAL NOT NULL,
  receipt_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
  observation_id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL,
  store TEXT NOT NULL,
  price REAL NOT NULL,
  unit_price REAL NOT NULL,
  unit TEXT NOT NULL,
  source_type TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  confidence REAL NOT NULL,
  verified INTEGER NOT NULL,
  valid_from TEXT NOT NULL DEFAULT '',
  valid_to TEXT NOT NULL DEFAULT '',
  conditions TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS watchlist (
  product_id TEXT PRIMARY KEY,
  buy_frequency_days INTEGER NOT NULL,
  target_price REAL NOT NULL,
  preferred_stores TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
  plan_id TEXT PRIMARY KEY,
  week TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  tool_calls TEXT NOT NULL,
  guardrail_events TEXT NOT NULL,
  tokens INTEGER NOT NULL,
  cost REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str = "smartpantry.db") -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(_SCHEMA)
        self._ensure_observation_columns()
        self.conn.commit()

    def _ensure_observation_columns(self) -> None:
        """Migrate pre-validity databases to the current schema."""
        columns = {row[1] for row in self.conn.execute(
            "PRAGMA table_info(observations)").fetchall()}
        for name, default in (("valid_from", "''"), ("valid_to", "''"),
                              ("conditions", "'[]'")):
            if name not in columns:
                self.conn.execute(
                    "ALTER TABLE observations ADD COLUMN %s TEXT NOT NULL"
                    " DEFAULT %s" % (name, default))

    def close(self) -> None:
        self.conn.close()

    # -- products ---------------------------------------------------
    def save_product(self, product: Product) -> None:
        errors = product.validate()
        if errors:
            raise ValueError("; ".join(errors))
        self.conn.execute(
            "INSERT OR REPLACE INTO products (product_id, data) VALUES (?, ?)",
            (product.product_id, json.dumps(product.to_dict())),
        )
        self.conn.commit()

    def get_product(self, product_id: str) -> Optional[Product]:
        row = self.conn.execute(
            "SELECT data FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        return Product.from_dict(json.loads(row[0])) if row else None

    def list_products(self) -> List[Product]:
        rows = self.conn.execute("SELECT data FROM products").fetchall()
        return [Product.from_dict(json.loads(r[0])) for r in rows]

    # -- purchases --------------------------------------------------
    def add_purchase(self, purchase: Purchase) -> None:
        errors = purchase.validate()
        if errors:
            raise ValueError("; ".join(errors))
        self.conn.execute(
            "INSERT OR REPLACE INTO purchases (purchase_id, product_id, date,"
            " store, quantity, price_paid, receipt_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (purchase.purchase_id, purchase.product_id, purchase.date,
             purchase.store, purchase.quantity, purchase.price_paid,
             purchase.receipt_id),
        )
        self.conn.commit()

    def purchases_for_product(self, product_id: str) -> List[Purchase]:
        rows = self.conn.execute(
            "SELECT purchase_id, date, store, product_id, quantity, price_paid,"
            " receipt_id FROM purchases WHERE product_id = ? ORDER BY date",
            (product_id,),
        ).fetchall()
        return [
            Purchase(purchase_id=r[0], date=r[1], store=r[2], product_id=r[3],
                     quantity=r[4], price_paid=r[5], receipt_id=r[6])
            for r in rows
        ]

    def last_basket_total(self) -> float:
        row = self.conn.execute(
            "SELECT receipt_id, SUM(price_paid) FROM purchases"
            " GROUP BY receipt_id ORDER BY MAX(date) DESC LIMIT 1"
        ).fetchone()
        return float(row[1]) if row else 0.0

    # -- observations -----------------------------------------------
    def add_observation(self, obs: PriceObservation) -> None:
        errors = obs.validate()
        if errors:
            raise ValueError("; ".join(errors))
        self.conn.execute(
            "INSERT OR REPLACE INTO observations (observation_id, product_id,"
            " store, price, unit_price, unit, source_type, fetched_at,"
            " confidence, verified, valid_from, valid_to, conditions)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (obs.observation_id, obs.product_id, obs.store, obs.price,
             obs.unit_price, obs.unit, obs.source_type, obs.fetched_at,
             obs.confidence, 1 if obs.verified else 0,
             obs.valid_from, obs.valid_to, json.dumps(obs.conditions)),
        )
        self.conn.commit()

    def observations_for_product(self, product_id: str,
                                 verified_only: bool = False) -> List[PriceObservation]:
        query = (
            "SELECT observation_id, product_id, store, price, unit_price,"
            " unit, source_type, fetched_at, confidence, verified,"
            " valid_from, valid_to, conditions"
            " FROM observations WHERE product_id = ?"
        )
        if verified_only:
            query += " AND verified = 1"
        query += " ORDER BY fetched_at"
        rows = self.conn.execute(query, (product_id,)).fetchall()
        return [
            PriceObservation(
                observation_id=r[0], product_id=r[1], store=r[2], price=r[3],
                unit_price=r[4], unit=r[5], source_type=r[6], fetched_at=r[7],
                confidence=r[8], verified=bool(r[9]),
                valid_from=r[10] or "", valid_to=r[11] or "",
                conditions=json.loads(r[12] or "[]"))
            for r in rows
        ]

    def price_history(self, product_id: str) -> List[float]:
        """Per-unit paid prices plus verified observed prices."""
        history: List[float] = []
        for purchase in self.purchases_for_product(product_id):
            if purchase.quantity > 0:
                history.append(purchase.price_paid / purchase.quantity)
        for obs in self.observations_for_product(product_id, verified_only=True):
            history.append(obs.price)
        return history

    # -- watchlist --------------------------------------------------
    def save_watchlist_item(self, item: WatchlistItem) -> None:
        errors = item.validate()
        if errors:
            raise ValueError("; ".join(errors))
        self.conn.execute(
            "INSERT OR REPLACE INTO watchlist (product_id, buy_frequency_days,"
            " target_price, preferred_stores) VALUES (?, ?, ?, ?)",
            (item.product_id, item.buy_frequency_days, item.target_price,
             json.dumps(item.preferred_stores)),
        )
        self.conn.commit()

    def list_watchlist(self) -> List[WatchlistItem]:
        rows = self.conn.execute(
            "SELECT product_id, buy_frequency_days, target_price,"
            " preferred_stores FROM watchlist"
        ).fetchall()
        return [
            WatchlistItem(product_id=r[0], buy_frequency_days=r[1],
                          target_price=r[2],
                          preferred_stores=json.loads(r[3]))
            for r in rows
        ]

    # -- plans ------------------------------------------------------
    def save_plan(self, plan: Plan) -> None:
        errors = plan.validate()
        if errors:
            raise ValueError("; ".join(errors))
        self.conn.execute(
            "INSERT OR REPLACE INTO plans (plan_id, week, data) VALUES (?, ?, ?)",
            (plan.plan_id, plan.week, json.dumps(plan.to_dict())),
        )
        self.conn.commit()

    def get_plan(self, plan_id: str) -> Optional[Plan]:
        row = self.conn.execute(
            "SELECT data FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        return Plan.from_dict(json.loads(row[0])) if row else None

    # -- run logs ---------------------------------------------------
    def log_run(self, entry: RunLog) -> None:
        errors = entry.validate()
        if errors:
            raise ValueError("; ".join(errors))
        self.conn.execute(
            "INSERT INTO run_logs (run_id, stage, tool_calls,"
            " guardrail_events, tokens, cost) VALUES (?, ?, ?, ?, ?, ?)",
            (entry.run_id, entry.stage, json.dumps(entry.tool_calls),
             json.dumps(entry.guardrail_events), entry.tokens, entry.cost),
        )
        self.conn.commit()

    def run_logs(self, run_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT stage, tool_calls, guardrail_events, tokens, cost"
            " FROM run_logs WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [
            {"stage": r[0], "tool_calls": json.loads(r[1]),
             "guardrail_events": json.loads(r[2]), "tokens": r[3], "cost": r[4]}
            for r in rows
        ]
