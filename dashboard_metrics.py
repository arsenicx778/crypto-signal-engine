import json
import os
from datetime import datetime, timedelta

from trade_store import get_trade_store
from time_utils import PACIFIC_TZ, now_pacific

COIN_CAPITAL_START = 1000.0
PORTFOLIO_CAPITAL_START = COIN_CAPITAL_START * 4
RISK_PERCENT = 0.02
REWARD_PERCENT = 0.03
COIN_ORDER = ["ETH", "SOL", "LINK", "XRP"]


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_signal_timestamp(value):
    if not value:
        return None

    raw = str(value).strip()
    if raw.endswith(" PT"):
        raw = raw[:-3].strip()

    parsed = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=PACIFIC_TZ)
    return parsed.astimezone(PACIFIC_TZ)


def _unpack_trade(trade: dict) -> dict:
    """Unpack metadata JSON into top-level keys for dashboard compat."""
    row = dict(trade)
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except Exception:
        meta = {}
    for k in ("ta_summary", "sentiment_summary", "history_summary",
              "decision_rationale", "overrides", "indicators",
              "tp_adjustments", "tp_adjustment_log"):
        row.setdefault(k, meta.get(k))
    # Translate state to CSV-compatible outcome field
    state = row.get("state", "")
    if state == "PENDING":
        row["outcome"] = "pending"
    elif state == "CLOSED":
        pass  # outcome already set (W/L)
    elif state == "DNE":
        row["outcome"] = "pending"
    return row


def load_rows_by_coin():
    """Load all trades from SQLite, grouped by coin with metadata unpacked."""
    store = get_trade_store()
    rows_by_coin = {}
    for coin in COIN_ORDER:
        try:
            trades = store.get_all_trades(coin=coin)
            rows_by_coin[coin] = [dict(_unpack_trade(t), coin=coin) for t in trades]
        except Exception:
            rows_by_coin[coin] = []
    return rows_by_coin


def get_all_signals(rows_by_coin=None):
    rows_by_coin = rows_by_coin or load_rows_by_coin()
    merged = []
    for coin in COIN_ORDER:
        merged.extend(rows_by_coin.get(coin, []))
    merged.sort(
        key=lambda row: (
            parse_signal_timestamp(row.get("timestamp")) or datetime.min.replace(tzinfo=PACIFIC_TZ),
            row.get("coin", ""),
        )
    )
    return merged


def _trade_rows(rows):
    return [row for row in rows if row.get("signal") in ("Buy", "Sell")]


def _closed_trade_rows(rows, closed_from=None, closed_to=None):
    closed = []
    for row in _trade_rows(rows):
        outcome = str(row.get("outcome", "")).strip()
        if outcome not in ("W", "L"):
            continue
        event_ts = parse_signal_timestamp(row.get("close_time")) or parse_signal_timestamp(row.get("timestamp"))
        if closed_from and (event_ts is None or event_ts < closed_from):
            continue
        if closed_to and (event_ts is None or event_ts > closed_to):
            continue
        closed.append(row)
    closed.sort(
        key=lambda row: (
            parse_signal_timestamp(row.get("close_time")) or parse_signal_timestamp(row.get("timestamp")) or datetime.min.replace(tzinfo=PACIFIC_TZ),
            parse_signal_timestamp(row.get("timestamp")) or datetime.min.replace(tzinfo=PACIFIC_TZ),
        )
    )
    return closed


def replay_capital(rows, capital_start=COIN_CAPITAL_START, closed_from=None, closed_to=None):
    capital = float(capital_start)
    equity_curve = []

    for row in _closed_trade_rows(rows, closed_from=closed_from, closed_to=closed_to):
        outcome = str(row.get("outcome", "")).strip()
        if outcome == "W":
            reward = safe_float(row.get("reward_amount"), 0.0)
            if reward <= 0:
                reward = round(capital * REWARD_PERCENT, 2)
            capital = round(capital + reward, 2)
        elif outcome == "L":
            risk = safe_float(row.get("risk_amount"), 0.0)
            if risk <= 0:
                risk = round(capital * RISK_PERCENT, 2)
            capital = round(capital - risk, 2)

        event_ts = parse_signal_timestamp(row.get("close_time")) or parse_signal_timestamp(row.get("timestamp"))
        equity_curve.append({"timestamp": event_ts, "capital": capital})

    return capital, equity_curve


def compute_drawdown(equity_curve, capital_start=COIN_CAPITAL_START):
    peak = float(capital_start)
    peak_ts = None
    trough = float(capital_start)
    trough_ts = None
    max_drawdown_pct = 0.0

    for point in equity_curve:
        capital = safe_float(point.get("capital"), capital_start)
        if capital > peak:
            peak = capital
            peak_ts = point.get("timestamp")
            trough = capital
            trough_ts = point.get("timestamp")

        if capital < trough:
            trough = capital
            trough_ts = point.get("timestamp")

        if peak > 0:
            drawdown_pct = max(0.0, (peak - capital) / peak * 100.0)
            if drawdown_pct > max_drawdown_pct:
                max_drawdown_pct = round(drawdown_pct, 2)

    return {
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "peak_capital": round(peak, 2),
        "peak_time": peak_ts.isoformat() if peak_ts else None,
        "trough_capital": round(trough, 2),
        "trough_time": trough_ts.isoformat() if trough_ts else None,
    }


def get_coin_stats(coin, rows_by_coin=None, capital_start=COIN_CAPITAL_START):
    rows_by_coin = rows_by_coin or load_rows_by_coin()
    rows = rows_by_coin.get(coin, [])
    trade_rows = _trade_rows(rows)
    wins = [row for row in trade_rows if row.get("outcome") == "W"]
    losses = [row for row in trade_rows if row.get("outcome") == "L"]
    pending = [row for row in trade_rows if row.get("outcome") == "pending"]
    open_trade = pending[-1] if pending else None

    capital, equity_curve = replay_capital(trade_rows, capital_start=capital_start)
    drawdown = compute_drawdown(equity_curve, capital_start=capital_start)

    completed = len(wins) + len(losses)
    win_rate = round(len(wins) / completed * 100, 1) if completed > 0 else 0.0

    cutoff_48h = now_pacific() - timedelta(hours=48)
    closed_48h = _closed_trade_rows(trade_rows, closed_from=cutoff_48h)
    wins_48h = sum(1 for r in closed_48h if r.get("outcome") == "W")
    completed_48h = len(closed_48h)
    win_rate_48h = round(wins_48h / completed_48h * 100, 1) if completed_48h > 0 else None

    return {
        "coin": coin,
        "capital": round(capital, 2),
        "wins": len(wins),
        "losses": len(losses),
        "pending": len(pending),
        "longs_open": sum(1 for row in pending if row.get("signal") == "Buy"),
        "shorts_open": sum(1 for row in pending if row.get("signal") == "Sell"),
        "win_rate": win_rate,
        "win_rate_48h": win_rate_48h,
        "completed_48h": completed_48h,
        "completed": completed,
        "open_trade": open_trade,
        "open_trades": pending,
        "risk_per_trade": round(capital * RISK_PERCENT, 2),
        "reward_per_trade": round(capital * REWARD_PERCENT, 2),
        "drawdown": drawdown,
        "signals_total": len(rows),
    }


def get_all_coin_stats(rows_by_coin=None):
    rows_by_coin = rows_by_coin or load_rows_by_coin()
    return [get_coin_stats(coin, rows_by_coin=rows_by_coin) for coin in COIN_ORDER]


def get_portfolio_stats(rows_by_coin=None):
    rows_by_coin = rows_by_coin or load_rows_by_coin()
    coin_stats = get_all_coin_stats(rows_by_coin=rows_by_coin)
    signals = get_all_signals(rows_by_coin=rows_by_coin)

    wins = sum(item["wins"] for item in coin_stats)
    losses = sum(item["losses"] for item in coin_stats)
    pending = [trade for item in coin_stats for trade in item["open_trades"]]
    pending.sort(
        key=lambda row: (
            parse_signal_timestamp(row.get("timestamp")) or datetime.min.replace(tzinfo=PACIFIC_TZ),
            row.get("coin", ""),
        )
    )
    completed = wins + losses
    total_capital = round(sum(item["capital"] for item in coin_stats), 2)

    loss_streak = 0
    closed_rows = _closed_trade_rows(signals)
    for row in reversed(closed_rows):
        if row.get("outcome") == "L":
            loss_streak += 1
        elif row.get("outcome") == "W":
            break

    cutoff_48h = now_pacific() - timedelta(hours=48)
    closed_48h = _closed_trade_rows(signals, closed_from=cutoff_48h)
    wins_48h = sum(1 for r in closed_48h if r.get("outcome") == "W")
    completed_48h = len(closed_48h)
    win_rate_48h = round(wins_48h / completed_48h * 100, 1) if completed_48h > 0 else None

    return {
        "capital": total_capital,
        "win_rate": round(wins / completed * 100, 1) if completed > 0 else 0.0,
        "win_rate_48h": win_rate_48h,
        "completed_48h": completed_48h,
        "wins": wins,
        "losses": losses,
        "pending_trades": len(pending),
        "pending_buys": len(pending),  # backward-compatible alias
        "total_signals": len(signals),
        "total_completed": completed,
        "loss_streak": loss_streak,
        "open_trades": pending,
        "open_trade": pending[-1] if pending else None,
        "coin_stats": coin_stats,
    }


def get_daily_signal_counts(signals, trailing_days=None, now=None):
    now = now or now_pacific()
    counts = {}

    for row in signals:
        ts = parse_signal_timestamp(row.get("timestamp"))
        if ts is None:
            continue
        day_key = ts.date().isoformat()
        counts[day_key] = counts.get(day_key, 0) + 1

    if trailing_days:
        days = []
        for offset in range(trailing_days - 1, -1, -1):
            day = (now - timedelta(days=offset)).date().isoformat()
            days.append({"date": day, "count": counts.get(day, 0)})
        return days

    return [{"date": day, "count": counts[day]} for day in sorted(counts)]


def get_signal_rate(signals, trailing_days=7, now=None):
    daily_counts = get_daily_signal_counts(signals, trailing_days=trailing_days, now=now)
    total = sum(item["count"] for item in daily_counts)
    days = max(1, len(daily_counts))
    average = round(total / days, 2)
    today_count = daily_counts[-1]["count"] if daily_counts else 0
    return {
        "daily_counts": daily_counts,
        "average_per_day": average,
        "today_count": today_count,
        "window_days": days,
        "total_in_window": total,
    }


def _date_range_bounds(date_start, date_end=None):
    start = parse_signal_timestamp(date_start)
    if start is None:
        return None, None
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)

    end = None
    if date_end and str(date_end).lower() != "ongoing":
        parsed_end = parse_signal_timestamp(date_end)
        if parsed_end is not None:
            end = parsed_end.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


def get_session_stats(project_data, rows_by_coin=None):
    rows_by_coin = rows_by_coin or load_rows_by_coin()
    sessions = project_data.get("sessions") or []
    current_session = int(project_data.get("current_session", 1) or 1)
    if current_session < 1 or current_session > len(sessions):
        return None

    session = sessions[current_session - 1]
    start_bound, end_bound = _date_range_bounds(session.get("date_start"), session.get("date_end"))
    coin_count = len(session.get("settings", {}).get("coins") or COIN_ORDER)
    capital_start = safe_float(session.get("results", {}).get("capital_start"), COIN_CAPITAL_START * coin_count)

    merged_signals = get_all_signals(rows_by_coin=rows_by_coin)
    trade_rows = _trade_rows(merged_signals)
    closed = _closed_trade_rows(trade_rows, closed_from=start_bound, closed_to=end_bound)

    capital_end, _curve = replay_capital(trade_rows, capital_start=capital_start, closed_from=start_bound, closed_to=end_bound)
    wins = sum(1 for row in closed if row.get("outcome") == "W")
    losses = sum(1 for row in closed if row.get("outcome") == "L")
    completed = wins + losses

    return {
        "session_number": session.get("session"),
        "capital_start": round(capital_start, 2),
        "capital_end": round(capital_end, 2),
        "trades": completed,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / completed * 100, 1) if completed > 0 else 0.0,
        "date_start": session.get("date_start"),
        "date_end": session.get("date_end"),
    }


def get_live_summary(rows_by_coin=None):
    rows_by_coin = rows_by_coin or load_rows_by_coin()
    signals = get_all_signals(rows_by_coin=rows_by_coin)
    portfolio = get_portfolio_stats(rows_by_coin=rows_by_coin)
    signal_rate = get_signal_rate(signals)

    all_drawdowns = {
        item["coin"]: item["drawdown"]
        for item in portfolio["coin_stats"]
    }
    max_drawdown_pct = max(
        (item["drawdown"]["max_drawdown_pct"] for item in portfolio["coin_stats"]),
        default=0.0,
    )
    profitable_coins = sum(1 for item in portfolio["coin_stats"] if item["capital"] > COIN_CAPITAL_START)

    return {
        "portfolio": portfolio,
        "signals_per_day": signal_rate,
        "per_coin_drawdown": all_drawdowns,
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "profitable_coins": profitable_coins,
    }
