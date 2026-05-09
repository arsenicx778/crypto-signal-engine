"""
Fix stale pending trades that are blocking the gates.
Scans SQLite for pending Buy/Sell trades older than 2 hours and marks them as losses.
"""

import sys
from datetime import datetime, timedelta
from trade_store import get_trade_store
from time_utils import now_pacific_str

STALE_THRESHOLD_MINUTES = 120  # 2 hours


def parse_timestamp(ts_str):
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(ts_str), fmt)
        except ValueError:
            continue
    return None


def fix_stale_trades(dry_run=True):
    store = get_trade_store()
    now = datetime.now()
    stale_threshold = now - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    action = "Would fix" if dry_run else "Fixing"
    print(f"\n{action} stale pending trades (older than {STALE_THRESHOLD_MINUTES} minutes)...")
    print("=" * 70)

    total_fixed = 0

    for coin in ["ETH", "SOL", "LINK", "XRP"]:
        pending = store.get_pending_trades(coin=coin)
        # Only actual Buy/Sell trades — not DNE signals
        actual_open = [t for t in pending if t.get("signal") in ("Buy", "Sell")]
        stale = []

        for trade in actual_open:
            entry_time = parse_timestamp(trade.get("timestamp"))
            if entry_time and entry_time < stale_threshold:
                stale.append(trade)

        if not stale:
            print(f"\n{coin}: No stale trades")
            continue

        print(f"\n{coin}: Found {len(stale)} stale trades")
        for trade in stale:
            entry_time = parse_timestamp(trade.get("timestamp"))
            age_hours = (now - entry_time).total_seconds() / 3600 if entry_time else 0
            print(f"  id={trade['id']} {trade['timestamp']} {trade['signal']:4s} @ {trade.get('entry_price')} (age: {age_hours:.1f}h)")

            if not dry_run:
                store.close_trade(
                    trade_id=trade["id"],
                    close_price=trade.get("entry_price") or 0,
                    close_time=now_pacific_str(),
                    outcome="L",
                )
                print(f"    → Marked as LOSS in SQLite")

        total_fixed += len(stale)

    print(f"\n{'=' * 70}")
    print(f"Total stale trades {action.lower()}: {total_fixed}")
    print(f"{'=' * 70}\n")
    return total_fixed


if __name__ == "__main__":
    apply_fix = "--apply" in sys.argv

    if apply_fix:
        print("APPLYING FIXES (--apply flag detected)")
        count = fix_stale_trades(dry_run=False)
        print(f"Fixed {count} stale trades")
    else:
        print("DRY RUN MODE (no changes will be made)")
        count = fix_stale_trades(dry_run=True)
        if count > 0:
            print(f"To apply: python fix_stale_trades.py --apply")
