"""
Integration layer between signal pipeline and SQLite trade store.
Routes signal creation and trade closure through trade_store.py instead of CSV.
Falls back to CSV for backwards compatibility if needed.
"""

import json
from datetime import datetime
from trade_store import get_trade_store
from time_utils import now_pacific_str
from typing import Optional, Dict, Any, List


def save_signal_to_store(
    coin_name: str,
    signal: Dict[str, Any],
    risk_amount: Optional[float] = None,
    reward_amount: Optional[float] = None,
) -> int:
    """
    Save a new signal to SQLite trade store.

    Args:
        coin_name: 'ETH', 'SOL', 'LINK', 'XRP'
        signal: dict with keys: signal, direction, confidence, entry_price, stop_loss,
                take_profit, reasoning
        risk_amount: amount at risk for this trade
        reward_amount: target profit for this trade

    Returns:
        trade_id from SQLite
    """
    store = get_trade_store()
    timestamp = now_pacific_str()

    metadata = {
        "ta_summary": signal.get("reasoning", {}).get("ta_summary"),
        "sentiment_summary": signal.get("reasoning", {}).get("sentiment_summary"),
        "history_summary": signal.get("reasoning", {}).get("history_summary"),
        "decision_rationale": signal.get("reasoning", {}).get("decision_rationale"),
    }

    trade_id = store.create_signal(
        coin=coin_name,
        timestamp=timestamp,
        signal=signal.get("signal"),
        direction=signal.get("direction"),
        confidence=signal.get("confidence", 0),
        entry_price=signal.get("entry_price"),
        stop_loss=signal.get("stop_loss"),
        take_profit=signal.get("take_profit"),
        risk_amount=risk_amount or 0,
        reward_amount=reward_amount or 0,
        metadata=metadata,
    )

    return trade_id


def close_signal_in_store(
    trade_id: int,
    close_price: float,
    outcome: str,  # "W" or "L"
) -> bool:
    """
    Close a trade in SQLite store.

    Args:
        trade_id: ID returned from save_signal_to_store()
        close_price: price at which trade closed
        outcome: "W" for win, "L" for loss

    Returns:
        True if successful, False if trade not found or already closed
    """
    store = get_trade_store()
    close_time = now_pacific_str()

    return store.close_trade(
        trade_id=trade_id,
        close_price=close_price,
        close_time=close_time,
        outcome=outcome,
    )


def get_pending_trades(coin_name: Optional[str] = None) -> List[Dict]:
    """Get all pending trades for a coin (or all coins)."""
    store = get_trade_store()
    return store.get_pending_trades(coin=coin_name)


def get_closed_trades(
    coin_name: Optional[str] = None,
    outcome: Optional[str] = None,
) -> List[Dict]:
    """Get closed trades for a coin (or all coins), optionally filtered by outcome."""
    store = get_trade_store()
    return store.get_closed_trades(coin=coin_name, outcome=outcome)


def get_trade_stats(coin_name: Optional[str] = None) -> Dict[str, Any]:
    """Get win/loss stats for a coin (or all coins)."""
    store = get_trade_store()
    return store.get_stats(coin=coin_name)


def find_trade_by_timestamp(timestamp: str, coin_name: Optional[str] = None) -> Optional[Dict]:
    """Find a trade by timestamp. Optionally filter by coin."""
    store = get_trade_store()
    all_pending = store.get_pending_trades(coin=coin_name)
    for trade in all_pending:
        if trade.get("timestamp") == timestamp:
            return trade

    # Also check closed trades
    all_closed = store.get_closed_trades(coin=coin_name)
    for trade in all_closed:
        if trade.get("timestamp") == timestamp:
            return trade

    return None


if __name__ == "__main__":
    # Test the integration
    test_signal = {
        "signal": "Buy",
        "direction": "LONG",
        "confidence": 75,
        "entry_price": 2500.0,
        "stop_loss": 2480.0,
        "take_profit": 2550.0,
        "reasoning": {
            "ta_summary": "RSI bullish, MACD crossover",
            "sentiment_summary": "positive news",
            "history_summary": "2 recent wins",
            "decision_rationale": "strong confluence"
        }
    }

    # Create a trade
    trade_id = save_signal_to_store("ETH", test_signal, risk_amount=50, reward_amount=75)
    print(f"Created trade ID: {trade_id}")

    # Query it
    pending = get_pending_trades("ETH")
    print(f"Pending trades: {len(pending)}")

    # Close it
    success = close_signal_in_store(trade_id, 2550.0, "W")
    print(f"Closed trade: {success}")

    # Check stats
    stats = get_trade_stats("ETH")
    print(f"Stats: {stats}")
