import os
from datetime import datetime, timedelta

from signal_store import read_latest_signals
from time_utils import PACIFIC_TZ, now_pacific

COIN_CAPITAL_START = 1000.0
PORTFOLIO_CAPITAL_START = COIN_CAPITAL_START * 4
RISK_PERCENT = 0.02
REWARD_PERCENT = 0.03
COIN_ORDER = ["ETH", "SOL", "AVAX", "XRP"]

_BASE = os.path.dirname(os.path.abspath(__file__))
COIN_CSV_FILES = {
    "ETH": os.path.join(_BASE, "eth_signals.csv"),
    "SOL": os.path.join(_BASE, "sol_signals.csv"),
    "AVAX": os.path.join(_BASE, "avax_signals.csv"),
    "XRP": os.path.join(_BASE, "xrp_signals.csv"),
}


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


def load_rows_by_coin(coin_csv_files=None):
    rows_by_coin = {}
    csv_map = coin_csv_files or COIN_CSV_FILES

    for coin in COIN_ORDER:
        csv_path = csv_map.get(coin)
        if not csv_path:
            rows_by_coin[coin] = []
            continue
        try:
            rows = read_latest_signals(csv_path)
        except Exception:
            rows = []
        annotated = []
        for row in rows:
            item = dict(row)
            item["coin"] = coin
            annotated.append(item)
        rows_by_coin[coin] = annotated
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

    return {
        "coin": coin,
        "capital": round(capital, 2),
        "wins": len(wins),
        "losses": len(losses),
        "pending": len(pending),
        "longs_open": sum(1 for row in pending if row.get("signal") == "Buy"),
        "shorts_open": sum(1 for row in pending if row.get("signal") == "Sell"),
        "win_rate": win_rate,
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

    return {
        "capital": total_capital,
        "win_rate": round(wins / completed * 100, 1) if completed > 0 else 0.0,
        "wins": wins,
        "losses": losses,
        "pending_trades": len(pending),
        "pending_buys": len(pending),  # backward-compatible alias for the existing dashboard payload
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
