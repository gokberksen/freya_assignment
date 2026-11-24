import logging
import sqlite3
from collections import defaultdict
from contextlib import closing, contextmanager
from pathlib import Path
from time import time
from uuid import uuid4

from .market import MINUTE, MarketError

logger = logging.getLogger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    symbol TEXT NOT NULL,
    target REAL NOT NULL CHECK (target > 0),
    direction TEXT NOT NULL CHECK (direction IN ('above', 'below')),
    mode TEXT NOT NULL CHECK (mode IN ('market', 'demo')),
    created_at INTEGER NOT NULL,
    next_minute INTEGER NOT NULL,
    triggered_at INTEGER,
    observed_price REAL,
    trigger_source TEXT CHECK (trigger_source IN ('market', 'manual'))
);
CREATE INDEX IF NOT EXISTS alerts_owner ON alerts(owner, mode);
CREATE INDEX IF NOT EXISTS alerts_pending ON alerts(mode, next_minute) WHERE triggered_at IS NULL;
"""


def now_ms():
    return int(time() * 1000)


class Alerts:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def list(self, owner, mode):
        with self.connect() as db:
            return [self.public(row) for row in db.execute(
                "SELECT * FROM alerts WHERE owner=? AND mode=? ORDER BY created_at DESC",
                (owner, mode),
            )]

    @staticmethod
    def public(row):
        keys = (
            "id", "symbol", "target", "direction", "created_at",
            "triggered_at", "observed_price", "trigger_source",
        )
        return {key: row[key] for key in keys}

    def get(self, owner, mode, alert_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM alerts WHERE owner=? AND mode=? AND id=?", (owner, mode, alert_id)
            ).fetchone()
            return self.public(row) if row else None

    def create(self, owner, mode, symbol, target, direction):
        created = now_ms()
        alert_id = uuid4().hex
        next_minute = (created + MINUTE - 1) // MINUTE * MINUTE
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM alerts WHERE owner=? AND mode=?", (owner, mode)
            ).fetchone()[0]
            if count >= 30:
                return None
            db.execute(
                """INSERT INTO alerts (id, owner, symbol, target, direction, mode, created_at, next_minute)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (alert_id, owner, symbol, target, direction, mode, created, next_minute),
            )
        return self.get(owner, mode, alert_id)

    def delete(self, owner, mode, alert_id):
        with self.connect() as db:
            return db.execute(
                "DELETE FROM alerts WHERE owner=? AND mode=? AND id=?", (owner, mode, alert_id)
            ).rowcount == 1

    def trigger_manual(self, owner, mode, alert_id):
        with self.connect() as db:
            db.execute(
                """UPDATE alerts SET triggered_at=?, observed_price=target, trigger_source='manual'
                   WHERE owner=? AND mode=? AND id=? AND triggered_at IS NULL""",
                (now_ms(), owner, mode, alert_id),
            )
        return self.get(owner, mode, alert_id)

    def check(self, market, current_ms=None):
        current_ms = current_ms if current_ms is not None else now_ms()
        latest_minute = (current_ms - market.delay_minutes * MINUTE) // MINUTE * MINUTE - MINUTE
        with self.connect() as db:
            waiting = db.execute(
                """SELECT * FROM alerts WHERE mode='market' AND triggered_at IS NULL
                   AND next_minute<=? ORDER BY next_minute""", (latest_minute,),
            ).fetchall()
        by_symbol = defaultdict(list)
        for alert in waiting:
            by_symbol[alert["symbol"]].append(alert)

        for symbol, alerts in by_symbol.items():
            # Only announce recent hits after an interruption. Leave fresh bars eligible.
            start = max(alerts[0]["next_minute"], latest_minute - 24 * 60 * MINUTE)
            try:
                bars = market.alert_bars(symbol, start, latest_minute)
            except MarketError:
                logger.warning("Alarm için fiyat verisi alınamadı (%s).", symbol)
                continue
            if not bars:
                continue
            with self.connect() as db:
                for alert in alerts:
                    hit = self.find_hit(alert, bars)
                    if hit:
                        when, observed = hit
                        db.execute(
                            """UPDATE alerts SET triggered_at=?, observed_price=?, trigger_source='market'
                               WHERE id=? AND triggered_at IS NULL""",
                            (when, observed, alert["id"]),
                        )
                    else:
                        db.execute(
                            """UPDATE alerts SET next_minute=max(next_minute, ?)
                               WHERE id=? AND triggered_at IS NULL""",
                            (bars[-1]["t"] + MINUTE, alert["id"]),
                        )

    @staticmethod
    def find_hit(alert, bars):
        for bar in bars:
            if bar["t"] < alert["next_minute"]:
                continue
            if alert["direction"] == "above" and bar["h"] >= alert["target"]:
                return bar["t"], bar["h"]
            if alert["direction"] == "below" and bar["l"] <= alert["target"]:
                return bar["t"], bar["l"]
        return None
