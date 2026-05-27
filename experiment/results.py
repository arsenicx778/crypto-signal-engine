"""
results.py — Writes experiment signals to trades.db with variant tag.

Uses raw SQLite — does not modify trade_store.py.
Simulates P&L by checking if price hit TP or SL after signal,
using the same close_price written by the live price monitor.
"""

import sqlite3
import os
from datetime import datetime

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trades.db")


def _get_conn():
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def save_experiment_signal(
    variant: str,
    coin: str,
    signal: str,
    direction: str | None,
    confidence: int,
    entry_price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
    risk_amount: float,
    reward_amount: float,
    metadata: str,
    timestamp: str,
) -> int | None:
    """
    Insert a new experiment signal row into trades.db with variant tag.
    Returns the row id or None on failure.
    """
    # Use EXPERIMENT state (not PENDING) so the live engine gate never counts these
    state = "DNE" if signal == "Do Not Enter" else "EXPERIMENT"
    now   = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with _get_conn() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO trades
                  (coin, timestamp, signal, direction, confidence, state,
                   entry_price, stop_loss, take_profit,
                   risk_amount, reward_amount,
                   metadata, variant, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    coin, timestamp, signal, direction, confidence, state,
                    entry_price, stop_loss, take_profit,
                    risk_amount, reward_amount,
                    metadata, variant, now, now,
                ),
            )
            return cur.lastrowid
    except Exception as e:
        print(f"[EXPERIMENT:results] save failed for variant={variant} coin={coin}: {e}")
        return None


def close_experiment_trade(
    trade_id: int,
    close_price: float,
    close_time: str,
    outcome: str,
) -> bool:
    """Mark an experiment PENDING trade as CLOSED with outcome W or L."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _get_conn() as conn:
            conn.execute(
                """
                UPDATE trades
                SET state='CLOSED', close_price=?, close_time=?, outcome=?, updated_at=?
                WHERE id=? AND state='EXPERIMENT'
                """,
                (close_price, close_time, outcome, now, trade_id),
            )
        return True
    except Exception as e:
        print(f"[EXPERIMENT:results] close failed for trade_id={trade_id}: {e}")
        return False


def get_pending_experiment_trades(variant: str, coin: str) -> list[dict]:
    """Return all PENDING trades for a given variant+coin."""
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, entry_price, stop_loss, take_profit, direction
                FROM trades
                WHERE variant=? AND coin=? AND state='EXPERIMENT'
                ORDER BY created_at
                """,
                (variant, coin),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[EXPERIMENT:results] get_pending failed: {e}")
        return []


def get_variant_stats() -> dict:
    """
    Return per-variant performance stats from trades.db.
    Used by the /api/variants dashboard endpoint.
    """
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                """
                SELECT
                  variant,
                  COUNT(*) as total,
                  SUM(CASE WHEN outcome='W' THEN 1 ELSE 0 END) as wins,
                  SUM(CASE WHEN outcome='L' THEN 1 ELSE 0 END) as losses,
                  ROUND(
                    100.0 * SUM(CASE WHEN outcome='W' THEN 1 ELSE 0 END)
                    / NULLIF(SUM(CASE WHEN outcome IN ('W','L') THEN 1 ELSE 0 END), 0),
                    1
                  ) as win_rate,
                  ROUND(AVG(confidence), 1) as avg_confidence,
                  ROUND(
                    SUM(CASE WHEN outcome='W' THEN reward_amount
                             WHEN outcome='L' THEN -risk_amount
                             ELSE 0 END),
                    2
                  ) as net_pnl,
                  SUM(CASE WHEN signal IN ('Buy','Sell') AND state='EXPERIMENT' THEN 1 ELSE 0 END) as open_trades,
                  MIN(created_at) as started_at
                FROM trades
                WHERE variant IS NOT NULL
                GROUP BY variant
                ORDER BY variant
                """
            ).fetchall()
        return {r["variant"]: dict(r) for r in rows}
    except Exception as e:
        print(f"[EXPERIMENT:results] get_variant_stats failed: {e}")
        return {}


def get_variant_daily_series(variant: str, days: int = 7) -> list[dict]:
    """
    Return daily win rate series for a variant (last N days).
    Used for the sparkline chart on the dashboard.
    """
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                """
                SELECT
                  date(close_time) as day,
                  SUM(CASE WHEN outcome='W' THEN 1 ELSE 0 END) as wins,
                  SUM(CASE WHEN outcome='L' THEN 1 ELSE 0 END) as losses,
                  ROUND(
                    100.0 * SUM(CASE WHEN outcome='W' THEN 1 ELSE 0 END)
                    / NULLIF(COUNT(*), 0), 1
                  ) as win_rate
                FROM trades
                WHERE variant=?
                  AND outcome IN ('W','L')
                  AND close_time >= datetime('now', ? || ' days')
                GROUP BY day
                ORDER BY day
                """,
                (variant, f"-{days}"),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[EXPERIMENT:results] get_variant_daily_series failed: {e}")
        return []
