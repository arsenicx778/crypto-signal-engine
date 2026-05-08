"""
Fix stale pending trades that are blocking the gates.
Scans all coins for pending trades older than 2 hours and marks them as losses.
"""

import os
from datetime import datetime, timedelta
from signal_store import read_latest_signals, append_signal_row
from time_utils import now_pacific_str

STALE_THRESHOLD_MINUTES = 120  # 2 hours


def parse_pacific_time(timestamp_str):
    """Parse Pacific timestamp like '2026-05-08 11:29:14'"""
    try:
        return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    except:
        return None


def fix_stale_trades(dry_run=True):
    """
    Scan all coin CSVs for stale pending trades and mark them as losses.

    Args:
        dry_run: If True, only print what would be done. If False, actually update trades.
    """
    coin_files = {
        "ETH": "eth_signals.csv",
        "SOL": "sol_signals.csv",
        "LINK": "link_signals.csv",
        "XRP": "xrp_signals.csv",
    }

    now = datetime.now()
    stale_threshold = now - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    action = "Would fix" if dry_run else "Fixing"
    print(f"\n{action} stale pending trades (older than {STALE_THRESHOLD_MINUTES} minutes)...")
    print("=" * 70)

    total_fixed = 0

    for coin_name, filepath in coin_files.items():
        if not os.path.exists(filepath):
            continue

        trades = read_latest_signals(filepath)
        stale_trades = []

        for trade in trades:
            if trade.get("signal") not in ("Buy", "Sell"):
                continue
            if trade.get("outcome") != "pending":
                continue

            timestamp_str = trade.get("timestamp", "")
            entry_time = parse_pacific_time(timestamp_str)
            if not entry_time:
                continue

            if entry_time < stale_threshold:
                stale_trades.append((entry_time, trade))

        if not stale_trades:
            print(f"\n{coin_name}: No stale trades")
            continue

        print(f"\n{coin_name}: Found {len(stale_trades)} stale trades")
        for entry_time, trade in stale_trades:
            age_hours = (now - entry_time).total_seconds() / 3600
            signal = trade["signal"]
            entry_price = trade["entry_price"]
            print(f"  {trade['timestamp']} {signal:4s} @ {entry_price} (age: {age_hours:.1f}h)")

            if not dry_run:
                # Mark as loss at last entry price (worst case)
                trade["outcome"] = "L"
                trade["close_price"] = trade.get("entry_price", 0)
                trade["close_time"] = now_pacific_str()
                append_signal_row(trade, filepath)
                print(f"    → Marked as LOSS")

            total_fixed += 1

    print(f"\n{'=' * 70}")
    print(f"Total stale trades {action.lower()}: {total_fixed}")
    print(f"{'=' * 70}\n")

    return total_fixed


if __name__ == "__main__":
    import sys

    # Check if --apply flag is provided
    apply_fix = "--apply" in sys.argv

    if apply_fix:
        print("⚠️  APPLYING FIXES (--apply flag detected)")
        count = fix_stale_trades(dry_run=False)
        print(f"Fixed {count} stale trades")
        print("Engine can now generate new signals for these coins")
    else:
        print("DRY RUN MODE (no changes will be made)")
        count = fix_stale_trades(dry_run=True)
        print(f"\nTo apply these fixes, run:")
        print(f"  python fix_stale_trades.py --apply")
