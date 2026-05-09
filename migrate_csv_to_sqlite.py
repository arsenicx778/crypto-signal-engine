"""
One-shot migration: reads all four coin CSVs and inserts historical trades into SQLite.
Deduplicates using the same key as signal_store (timestamp, signal, entry_price, stop_loss),
keeps the latest version of each row, and skips rows already in the DB.

Run once:  python migrate_csv_to_sqlite.py
"""

import json
import sqlite3
from signal_store import parse_coin_csv
from trade_store import get_trade_store
from time_utils import now_pacific_str

COINS = {
    "ETH":  "eth_signals.csv",
    "SOL":  "sol_signals.csv",
    "LINK": "link_signals.csv",
    "XRP":  "xrp_signals.csv",
}


def deduplicate(rows):
    """Keep latest version of each (timestamp, signal, entry_price, stop_loss) tuple."""
    seen = {}
    order = []
    for row in rows:
        key = (
            row.get("timestamp", ""),
            row.get("signal", ""),
            str(row.get("entry_price", "")),
            str(row.get("stop_loss", "")),
        )
        if key not in seen:
            order.append(key)
        seen[key] = row
    return [seen[k] for k in order]


def csv_row_to_db(coin, row):
    """Convert a CSV row dict to kwargs for create_signal / direct INSERT."""
    signal = row.get("signal", "")
    outcome = row.get("outcome", "pending")

    if signal == "Do Not Enter":
        state = "DNE"
    elif outcome in ("W", "L"):
        state = "CLOSED"
    else:
        state = "PENDING"

    metadata = json.dumps({
        "ta_summary":         row.get("ta_summary") or "",
        "sentiment_summary":  row.get("sentiment_summary") or "",
        "history_summary":    row.get("history_summary") or "",
        "decision_rationale": row.get("decision_rationale") or "",
        "overrides":          row.get("overrides") or "",
        "indicators":         row.get("indicators") or "",
        "tp_adjustments":     row.get("tp_adjustments") or 0,
        "tp_adjustment_log":  row.get("tp_adjustment_log") or "",
    })

    def _f(v):
        try:
            return float(v) if v not in (None, "", "None") else None
        except (ValueError, TypeError):
            return None

    def _i(v):
        try:
            return int(v) if v not in (None, "", "None") else 0
        except (ValueError, TypeError):
            return 0

    return {
        "coin":          coin,
        "timestamp":     row.get("timestamp", ""),
        "signal":        signal,
        "direction":     row.get("direction") or None,
        "confidence":    _i(row.get("confidence")),
        "state":         state,
        "entry_price":   _f(row.get("entry_price")),
        "stop_loss":     _f(row.get("stop_loss")),
        "take_profit":   _f(row.get("take_profit")),
        "risk_amount":   _f(row.get("risk_amount")) or 0,
        "reward_amount": _f(row.get("reward_amount")) or 0,
        "close_price":   _f(row.get("close_price")),
        "close_time":    row.get("close_time") or None,
        "outcome":       outcome if outcome in ("W", "L") else None,
        "metadata":      metadata,
    }


def migrate():
    store = get_trade_store()
    now = now_pacific_str()

    total_inserted = 0
    total_skipped = 0

    for coin, csv_path in COINS.items():
        print(f"\n[{coin}] Reading {csv_path}...")
        try:
            raw_rows = parse_coin_csv(csv_path)
        except Exception as e:
            print(f"  Error reading CSV: {e}")
            continue

        deduped = deduplicate(raw_rows)
        print(f"  {len(raw_rows)} raw rows → {len(deduped)} after dedup")

        inserted = skipped = 0

        with store.get_connection() as conn:
            for row in deduped:
                db = csv_row_to_db(coin, row)
                if not db["timestamp"]:
                    skipped += 1
                    continue

                # Check if already exists by (coin, timestamp, signal, entry_price)
                existing = conn.execute(
                    "SELECT id FROM trades WHERE coin=? AND timestamp=? AND signal=? AND entry_price IS ?",
                    (db["coin"], db["timestamp"], db["signal"],
                     db["entry_price"]),
                ).fetchone()

                if existing:
                    skipped += 1
                    continue

                conn.execute(
                    """
                    INSERT INTO trades (
                        coin, timestamp, signal, direction, confidence, state,
                        entry_price, stop_loss, take_profit, risk_amount, reward_amount,
                        close_price, close_time, outcome,
                        metadata, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        db["coin"], db["timestamp"], db["signal"], db["direction"],
                        db["confidence"], db["state"],
                        db["entry_price"], db["stop_loss"], db["take_profit"],
                        db["risk_amount"], db["reward_amount"],
                        db["close_price"], db["close_time"], db["outcome"],
                        db["metadata"], now, now,
                    ),
                )
                inserted += 1

        print(f"  Inserted: {inserted}  Skipped (already in DB): {skipped}")
        total_inserted += inserted
        total_skipped += skipped

    print(f"\n{'='*50}")
    print(f"Migration complete: {total_inserted} inserted, {total_skipped} skipped")
    print(f"{'='*50}")

    # Final count
    store2 = get_trade_store()
    print("\nPost-migration DB counts:")
    for coin in COINS:
        stats = store2.get_stats(coin=coin)
        all_t = store2.get_all_trades(coin=coin)
        print(f"  {coin}: {len(all_t)} total  W={stats['wins']} L={stats['losses']} pending={stats['pending']}")


if __name__ == "__main__":
    migrate()
