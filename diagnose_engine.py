"""
Diagnostic script — reads only from SQLite and live API. No CSV.
"""

import os
import json
import requests
from datetime import datetime
from trade_store import get_trade_store


def check_api_connectivity():
    print("\n[API CHECK]")
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": "XETHZUSD"},
            timeout=5,
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


def check_config():
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


def check_engine_state():
    print("\n[ENGINE STATE]")
    try:
        with open("engine_state.json") as f:
            state = json.load(f)
        print(f"  Cycle counter: {state.get('cycle_counter')}")
        for coin, cs in state.get("coins", {}).items():
            cap = cs.get("capital", 0)
            ol = cs.get("open_longs", 0)
            os_ = cs.get("open_shorts", 0)
            print(f"  {coin}: capital=${cap:.2f}  L:{ol} S:{os_}")
    except Exception as e:
        print(f"  Error reading engine state: {e}")


def check_database():
    print("\n[DATABASE — TRADE COUNTS]")
    store = get_trade_store()
    now = datetime.now()
    grand_total = 0
    any_stale = False

    for coin in ["ETH", "SOL", "LINK", "XRP"]:
        stats = store.get_stats(coin=coin)
        all_trades = store.get_all_trades(coin=coin)
        pending = store.get_pending_trades(coin=coin)
        actual_open = [t for t in pending if t.get("signal") in ("Buy", "Sell")]

        stale_open = []
        for t in actual_open:
            ts_str = t.get("timestamp", "")
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                age_hours = (now - ts).total_seconds() / 3600
                if age_hours > 2:
                    stale_open.append((t, age_hours))
            except Exception:
                pass

        total_trades = len(all_trades)
        grand_total += total_trades
        stale_flag = f"  ⚠ {len(stale_open)} STALE open trades!" if stale_open else ""
        print(f"  {coin}: {total_trades} total  W={stats['wins']} L={stats['losses']} "
              f"pending={stats['pending']}{stale_flag}")

        for t, age in stale_open:
            print(f"    └ id={t['id']} {t['timestamp']} {t['signal']} age={age:.1f}h")
            any_stale = True

    print(f"\n  Grand total in DB: {grand_total} trades")
    if any_stale:
        print("\n  ⚠ Stale open trades found — run: python fix_stale_trades.py --apply")


def check_last_signal():
    print("\n[LAST SIGNAL PER COIN]")
    store = get_trade_store()
    now = datetime.now()
    for coin in ["ETH", "SOL", "LINK", "XRP"]:
        all_trades = store.get_all_trades(coin=coin)
        if not all_trades:
            print(f"  {coin}: No trades in DB")
            continue
        last = all_trades[-1]
        ts_str = last.get("timestamp", "unknown")
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            age_min = (now - ts).total_seconds() / 60
            flag = "  ⚠ >60min ago!" if age_min > 60 else ""
            print(f"  {coin}: last signal at {ts_str} ({age_min:.0f}m ago){flag}")
        except Exception:
            print(f"  {coin}: last signal at {ts_str}")


def diagnose():
    print("=" * 60)
    print("  CRYPTO SIGNAL ENGINE — DIAGNOSTICS")
    print("=" * 60)

    check_api_connectivity()
    check_config()
    check_engine_state()
    check_database()
    check_last_signal()

    print("\n" + "=" * 60)
    print("QUICK FIXES")
    print("=" * 60)
    print("""
  Stale trades blocking gates:  python fix_stale_trades.py --apply
  View full health report:       python health_check.py
  View trade dashboard:          python dashboard_sqlite.py
  Test a single coin cycle:      python -c "from main import run_cycle, COINS; run_cycle(COINS[0])"
    """)


if __name__ == "__main__":
    diagnose()
