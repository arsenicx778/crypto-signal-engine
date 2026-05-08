"""
Dashboard for viewing trading statistics from SQLite store.
Replaces CSV-based queries with SQLite backend.
"""

import json
from trade_store import get_trade_store
from datetime import datetime, timedelta


def print_header(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def format_stats(stats, coin_name):
    """Pretty-print trade statistics."""
    print(f"\n{coin_name}:")
    print(f"  Wins:    {stats['wins']}")
    print(f"  Losses:  {stats['losses']}")
    print(f"  Total:   {stats['total']}")
    print(f"  Win%:    {stats['win_rate']:.1f}%")
    print(f"  Pending: {stats['pending']}")


def print_recent_trades(trades, limit=10):
    """Print recent closed trades."""
    print(f"\nRecent trades (last {limit}):")
    for trade in trades[:limit]:
        timestamp = trade.get("timestamp", "?")
        signal = trade.get("signal", "?")
        direction = trade.get("direction", "?")
        entry = trade.get("entry_price", "?")
        outcome = trade.get("outcome", "?")
        close_price = trade.get("close_price", "?")
        risk = trade.get("risk_amount", 0)
        reward = trade.get("reward_amount", 0)

        outcome_char = "W" if outcome == "W" else "L" if outcome == "L" else "?"
        entry_str = f"${float(entry):,.2f}" if entry else "?"
        close_str = f"${float(close_price):,.2f}" if close_price else "?"
        print(f"  {timestamp} {signal:4s} {direction:5s} entry:{entry_str} close:{close_str} {outcome_char}")


def print_pending_trades(trades):
    """Print pending trades waiting for close."""
    print(f"\nPending trades ({len(trades)}):")
    if not trades:
        print("  (none)")
        return
    for trade in trades:
        timestamp = trade.get("timestamp", "?")
        signal = trade.get("signal", "?")
        direction = trade.get("direction", "?")
        entry = trade.get("entry_price", "?")
        tp = trade.get("take_profit", "?")
        sl = trade.get("stop_loss", "?")
        entry_str = f"${float(entry):,.2f}" if entry else "?"
        tp_str = f"${float(tp):,.2f}" if tp else "?"
        sl_str = f"${float(sl):,.2f}" if sl else "?"
        print(f"  {timestamp} {signal:4s} {direction:5s} entry:{entry_str} TP:{tp_str} SL:{sl_str}")


def dashboard():
    """Display full trading dashboard from SQLite."""
    store = get_trade_store()

    print_header("CRYPTO SIGNAL ENGINE — SQLITE DASHBOARD")

    coins = ["ETH", "SOL", "LINK", "XRP"]
    all_stats = {}

    # Collect all stats
    for coin in coins:
        stats = store.get_stats(coin=coin)
        all_stats[coin] = stats

    # Summary table
    print("\nOVERALL STATS:")
    print(f"{'Coin':<6} {'Wins':<6} {'Loss':<6} {'Total':<6} {'Win%':<8} {'Pending':<8}")
    print("─" * 50)
    for coin in coins:
        stats = all_stats[coin]
        print(f"{coin:<6} {stats['wins']:<6} {stats['losses']:<6} {stats['total']:<6} "
              f"{stats['win_rate']:<8.1f} {stats['pending']:<8}")

    # Per-coin details
    for coin in coins:
        print_header(f"{coin} TRADES")
        stats = all_stats[coin]
        format_stats(stats, coin)

        # Recent closed trades
        closed = store.get_closed_trades(coin=coin)
        if closed:
            print_recent_trades(closed, limit=5)

        # Pending trades
        pending = store.get_pending_trades(coin=coin)
        print_pending_trades(pending)

    # Aggregate stats
    print_header("AGGREGATE STATS")
    total_wins = sum(all_stats[c]["wins"] for c in coins)
    total_losses = sum(all_stats[c]["losses"] for c in coins)
    total_trades = total_wins + total_losses
    total_pending = sum(all_stats[c]["pending"] for c in coins)
    overall_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0

    print(f"\nAll Coins Combined:")
    print(f"  Total Trades:  {total_trades}")
    print(f"  Wins:          {total_wins}")
    print(f"  Losses:        {total_losses}")
    print(f"  Win Rate:      {overall_wr:.1f}%")
    print(f"  Pending:       {total_pending}")

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    dashboard()
