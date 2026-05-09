"""
Clean trade storage using SQLite instead of CSV.
Tracks trades with three states: PENDING, CLOSED (won/lost), DNE (skipped).
"""

import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

DB_PATH = "trades.db"


class TradeStore:
    """SQLite-backed trade tracking."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_db()

    @contextmanager
    def get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self):
        """Create tables if they don't exist."""
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    direction TEXT,
                    confidence INTEGER,
                    state TEXT NOT NULL DEFAULT 'PENDING',

                    entry_price REAL,
                    entry_time TEXT,
                    stop_loss REAL,
                    take_profit REAL,
                    risk_amount REAL,
                    reward_amount REAL,

                    close_price REAL,
                    close_time TEXT,
                    outcome TEXT,

                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,

                    UNIQUE(coin, timestamp, signal, entry_price)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_coin_state
                ON trades(coin, state)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON trades(timestamp)
            """)

    def create_signal(
        self,
        coin: str,
        timestamp: str,
        signal: str,
        direction: Optional[str] = None,
        confidence: int = 0,
        entry_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        risk_amount: float = 0,
        reward_amount: float = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Create a new signal (PENDING or DNE).

        Returns: trade_id
        """
        now = datetime.utcnow().isoformat()
        meta_json = json.dumps(metadata or {})

        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (
                    coin, timestamp, signal, direction, confidence, state,
                    entry_price, stop_loss, take_profit, risk_amount, reward_amount,
                    metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    coin, timestamp, signal, direction, confidence,
                    "DNE" if signal == "Do Not Enter" else "PENDING",
                    entry_price, stop_loss, take_profit, risk_amount, reward_amount,
                    meta_json, now, now,
                ),
            )
            return cursor.lastrowid

    def close_trade(
        self,
        trade_id: int,
        close_price: float,
        close_time: str,
        outcome: str,  # "W" or "L"
    ) -> bool:
        """
        Close a pending trade. Marks it as CLOSED with outcome.

        Returns: True if successful
        """
        now = datetime.utcnow().isoformat()

        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE trades
                SET state='CLOSED', close_price=?, close_time=?, outcome=?, updated_at=?
                WHERE id=? AND state='PENDING'
                """,
                (close_price, close_time, outcome, now, trade_id),
            )
            return cursor.rowcount > 0

    def get_pending_trades(self, coin: Optional[str] = None) -> List[Dict]:
        """Get all pending trades, optionally filtered by coin."""
        with self.get_connection() as conn:
            if coin:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE coin=? AND state='PENDING' ORDER BY timestamp",
                    (coin,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE state='PENDING' ORDER BY timestamp"
                ).fetchall()

            return [dict(row) for row in rows]

    def get_closed_trades(
        self,
        coin: Optional[str] = None,
        outcome: Optional[str] = None,  # "W", "L", or None for both
    ) -> List[Dict]:
        """Get closed trades, optionally filtered by coin and/or outcome."""
        with self.get_connection() as conn:
            if coin and outcome:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE coin=? AND state='CLOSED' AND outcome=? ORDER BY close_time DESC",
                    (coin, outcome),
                ).fetchall()
            elif coin:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE coin=? AND state='CLOSED' ORDER BY close_time DESC",
                    (coin,),
                ).fetchall()
            elif outcome:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE state='CLOSED' AND outcome=? ORDER BY close_time DESC",
                    (outcome,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE state='CLOSED' ORDER BY close_time DESC"
                ).fetchall()

            return [dict(row) for row in rows]

    def get_stats(self, coin: Optional[str] = None) -> Dict[str, Any]:
        """Calculate trading stats for a coin or all coins."""
        with self.get_connection() as conn:
            if coin:
                closed = conn.execute(
                    "SELECT outcome FROM trades WHERE coin=? AND state='CLOSED'",
                    (coin,),
                ).fetchall()
            else:
                closed = conn.execute(
                    "SELECT outcome FROM trades WHERE state='CLOSED'"
                ).fetchall()

            wins = sum(1 for row in closed if row[0] == "W")
            losses = sum(1 for row in closed if row[0] == "L")
            total = wins + losses
            win_rate = (wins / total * 100) if total > 0 else 0

            if coin:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE coin=? AND state='PENDING'",
                    (coin,),
                ).fetchone()[0]
            else:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE state='PENDING'"
                ).fetchone()[0]

            return {
                "wins": wins,
                "losses": losses,
                "total": total,
                "win_rate": round(win_rate, 1),
                "pending": pending,
            }

    def get_all_trades(self, coin: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
        """Get ALL trades (PENDING, CLOSED, DNE) ordered by timestamp asc."""
        with self.get_connection() as conn:
            if coin:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE coin=? ORDER BY timestamp ASC",
                    (coin,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY timestamp ASC"
                ).fetchall()
            result = [dict(row) for row in rows]
            if limit:
                result = result[-limit:]
            return result

    def get_recent_trades(self, coin: str, n: int = 10) -> List[Dict]:
        """Get the last N trades for a coin (all states)."""
        return self.get_all_trades(coin=coin, limit=n)

    def get_completed_trades(self, coin: Optional[str] = None) -> List[Dict]:
        """Get all CLOSED trades with outcome W or L, ordered by close_time asc."""
        with self.get_connection() as conn:
            if coin:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE coin=? AND state='CLOSED' AND outcome IN ('W','L') ORDER BY close_time ASC",
                    (coin,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE state='CLOSED' AND outcome IN ('W','L') ORDER BY close_time ASC"
                ).fetchall()
            return [dict(row) for row in rows]

    def get_current_capital(self, coin: str, capital_start: float = 1000.0) -> float:
        """Replay closed trades to calculate current capital for a coin."""
        capital = capital_start
        completed = self.get_completed_trades(coin=coin)
        for trade in completed:
            outcome = trade.get("outcome")
            risk = trade.get("risk_amount") or 0
            reward = trade.get("reward_amount") or 0
            if outcome == "W":
                capital += float(reward) if reward else round(capital * 0.03, 2)
            elif outcome == "L":
                capital -= float(risk) if risk else round(capital * 0.02, 2)
        return round(capital, 2)

    def count_todays_signals(self, coin: str, today_str: str) -> int:
        """Count all signals (any state) for a coin on a given date (YYYY-MM-DD)."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE coin=? AND timestamp LIKE ?",
                (coin, f"{today_str}%"),
            ).fetchone()
            return row[0] if row else 0

    def update_trade_tp(
        self,
        trade_id: int,
        new_tp: float,
        adjustment_count: int,
        adjustment_log: str,
    ) -> bool:
        """Update take_profit and adjustment metadata on a PENDING trade."""
        now = datetime.utcnow().isoformat()
        with self.get_connection() as conn:
            # Update take_profit and store adjustment data in metadata
            trade_row = conn.execute(
                "SELECT metadata FROM trades WHERE id=? AND state='PENDING'",
                (trade_id,),
            ).fetchone()
            if not trade_row:
                return False
            try:
                meta = json.loads(trade_row[0] or "{}")
            except Exception:
                meta = {}
            meta["tp_adjustments"] = adjustment_count
            meta["tp_adjustment_log"] = adjustment_log
            cursor = conn.execute(
                """UPDATE trades
                   SET take_profit=?, metadata=?, updated_at=?
                   WHERE id=? AND state='PENDING'""",
                (new_tp, json.dumps(meta), now, trade_id),
            )
            return cursor.rowcount > 0

    def find_by_timestamp(self, timestamp: str, coin: Optional[str] = None) -> Optional[Dict]:
        """Find a trade by exact timestamp, optionally filtered by coin."""
        with self.get_connection() as conn:
            if coin:
                row = conn.execute(
                    "SELECT * FROM trades WHERE coin=? AND timestamp=? ORDER BY id DESC LIMIT 1",
                    (coin, timestamp),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM trades WHERE timestamp=? ORDER BY id DESC LIMIT 1",
                    (timestamp,),
                ).fetchone()
            return dict(row) if row else None

    def export_to_csv(self, output_path: str, coin: Optional[str] = None):
        """Export trades to CSV for analysis."""
        import csv

        with self.get_connection() as conn:
            if coin:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE coin=? ORDER BY timestamp",
                    (coin,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY timestamp"
                ).fetchall()

        with open(output_path, "w", newline="") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=dict(rows[0]).keys())
                writer.writeheader()
                writer.writerows([dict(row) for row in rows])


# Singleton instance
_store = None


def get_trade_store() -> TradeStore:
    """Get or create the global trade store."""
    global _store
    if _store is None:
        _store = TradeStore()
    return _store
