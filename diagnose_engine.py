"""
Diagnostic script to identify why coin engines stopped generating signals.
Checks: API connectivity, recent CSV updates, engine state, and logs.
"""

import os
import json
import requests
from datetime import datetime
import time


def check_api_connectivity():
    """Test Kraken API connectivity."""
    print("\n[API CHECK]")
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": "XETHZUSD"},
            timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("result"):
                price = float(list(data["result"].values())[0]["c"][0])
                print(f"  ✓ Kraken API responsive — ETH: ${price:,.2f}")
                return True
    except Exception as e:
        print(f"  ✗ Kraken API error: {e}")
        return False


def check_file_timestamps():
    """Check when each coin's CSV was last updated."""
    print("\n[FILE TIMESTAMPS]")
    coin_files = {
        "ETH": "eth_signals.csv",
        "SOL": "sol_signals.csv",
        "LINK": "link_signals.csv",
        "XRP": "xrp_signals.csv",
    }

    now = datetime.now()
    for coin, filepath in coin_files.items():
        if os.path.exists(filepath):
            mod_time = datetime.fromtimestamp(os.path.getmtime(filepath))
            elapsed = now - mod_time
            minutes = elapsed.total_seconds() / 60
            print(f"  {coin}: {filepath} updated {minutes:.0f} minutes ago")
            if minutes > 60:
                print(f"       ⚠ No updates in over an hour!")
        else:
            print(f"  {coin}: {filepath} NOT FOUND")


def check_csv_row_counts():
    """Count trades in each CSV."""
    print("\n[CSV ROW COUNTS]")
    coin_files = {
        "ETH": "eth_signals.csv",
        "SOL": "sol_signals.csv",
        "LINK": "link_signals.csv",
        "XRP": "xrp_signals.csv",
    }

    for coin, filepath in coin_files.items():
        if os.path.exists(filepath):
            with open(filepath) as f:
                lines = f.readlines()
            row_count = len(lines) - 1  # subtract header
            print(f"  {coin}: {row_count} trades")
        else:
            print(f"  {coin}: file not found")


def check_engine_state():
    """Check engine_state.json for coin status."""
    print("\n[ENGINE STATE]")
    try:
        with open("engine_state.json") as f:
            state = json.load(f)
        print(f"  Cycle counter: {state.get('cycle_counter')}")
        coins = state.get("coins", {})
        for coin_name, coin_state in coins.items():
            print(f"  {coin_name}:")
            print(f"    Capital: ${coin_state.get('capital', 0):.2f}")
            print(f"    Open longs:  {coin_state.get('open_longs', 0)}")
            print(f"    Open shorts: {coin_state.get('open_shorts', 0)}")
    except Exception as e:
        print(f"  Error reading engine state: {e}")


def check_config():
    """Check if live trading is enabled."""
    print("\n[CONFIG CHECK]")
    try:
        from config import LIVE_TRADING_ENABLED, LIVE_TRADING_PAUSE_REASON, SUSPENDED_COINS
        print(f"  Live trading enabled: {LIVE_TRADING_ENABLED}")
        if LIVE_TRADING_PAUSE_REASON:
            print(f"  Pause reason: {LIVE_TRADING_PAUSE_REASON}")
        if SUSPENDED_COINS:
            print(f"  Suspended coins: {SUSPENDED_COINS}")
    except Exception as e:
        print(f"  Error reading config: {e}")


def check_database():
    """Check SQLite database."""
    print("\n[DATABASE CHECK]")
    try:
        from trade_store import get_trade_store
        store = get_trade_store()

        total_trades = 0
        for coin in ["ETH", "SOL", "LINK", "XRP"]:
            stats = store.get_stats(coin=coin)
            closed = stats['wins'] + stats['losses']
            pending = stats['pending']
            total = closed + pending
            total_trades += total
            if total > 0:
                print(f"  {coin}: {closed} closed, {pending} pending")

        if total_trades == 0:
            print(f"  No trades in SQLite yet (still using CSV)")
    except Exception as e:
        print(f"  Error reading database: {e}")


def diagnose():
    """Run all diagnostics."""
    print("="*60)
    print("  CRYPTO SIGNAL ENGINE — DIAGNOSTICS")
    print("="*60)

    check_api_connectivity()
    check_config()
    check_file_timestamps()
    check_csv_row_counts()
    check_engine_state()
    check_database()

    print("\n" + "="*60)
    print("RECOMMENDATIONS:")
    print("="*60)
    print("""
1. If API is down: wait for Kraken API to recover
2. If timestamps are old: engine may be crashing — check console logs
3. If engine_state shows cycle_counter growing: engine IS running
4. Check for exceptions in the cycle by running with more verbose logging
5. Verify all imports work: python -c "from main import *"
    """)


if __name__ == "__main__":
    diagnose()
