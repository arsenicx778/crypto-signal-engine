import json
import os
from datetime import datetime
from time_utils import now_pacific

PROJECT_LOG = os.path.join(os.path.dirname(__file__), "project_log.json")

RISK_PERCENT   = 0.02  # 2% of coin capital
REWARD_PERCENT = 0.03  # 3% of coin capital


def load_log():
    if not os.path.exists(PROJECT_LOG):
        return None
    with open(PROJECT_LOG, "r") as f:
        return json.load(f)


def save_log(data):
    tmp_path = PROJECT_LOG + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, PROJECT_LOG)


def _safe_number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ensure_per_coin_entry(per_coin, coin, base_capital=None):
    if not isinstance(per_coin, dict):
        per_coin = {}

    entry = per_coin.get(coin)
    if not isinstance(entry, dict):
        entry = {}

    if base_capital is not None and "capital" not in entry:
        entry["capital"] = round(_safe_number(base_capital, 1000.0), 2)

    entry.setdefault("capital", 1000)
    entry.setdefault("trades", 0)
    entry.setdefault("wins", 0)
    entry.setdefault("losses", 0)
    entry.setdefault("win_rate", 0)
    entry.setdefault("long_trades", 0)
    entry.setdefault("short_trades", 0)

    per_coin[coin] = entry
    return per_coin, entry


def record_trade_outcome(outcome, confidence, entry_price, close_price, coin="ETH", direction="LONG",
                         risk_amount=None, reward_amount=None):
    try:
        data = load_log()
        if not data:
            return

        is_win  = outcome == "W"
        is_loss = outcome == "L"

        if not (is_win or is_loss):
            return

        session_idx = data["current_session"] - 1
        session     = data["sessions"][session_idx]

        # Use dynamic amounts if provided, else fall back to % of current session capital
        _capital_end = _safe_number(session["results"].get("capital_end", 0), 0.0)
        _reward = reward_amount if reward_amount is not None else round(_capital_end * REWARD_PERCENT, 2)
        _risk   = risk_amount   if risk_amount   is not None else round(_capital_end * RISK_PERCENT,   2)

        session["results"]["trades"] += 1
        if is_win:
            session["results"]["wins"]        += 1
            session["results"]["capital_end"] = round(_capital_end + _reward, 2)
        else:
            session["results"]["losses"]      += 1
            session["results"]["capital_end"] = round(_capital_end - _risk, 2)

        total = session["results"]["wins"] + session["results"]["losses"]
        session["results"]["win_rate"] = round(
            session["results"]["wins"] / total * 100
        ) if total > 0 else 0

        data["totals"]["all_time_trades"] += 1
        if is_win:
            data["totals"]["all_time_wins"] += 1
        else:
            data["totals"]["all_time_losses"] += 1

        all_total = data["totals"]["all_time_wins"] + data["totals"]["all_time_losses"]
        data["totals"]["all_time_win_rate"] = round(
            data["totals"]["all_time_wins"] / all_total * 100
        ) if all_total > 0 else 0

        if session["results"]["capital_end"] > _safe_number(data["totals"].get("peak_capital", 0), 0.0):
            data["totals"]["peak_capital"] = round(session["results"]["capital_end"], 2)

        # Update per_coin breakdown (trades, wins, losses, win_rate, capital)
        per_coin, pc = _ensure_per_coin_entry(
            data["totals"].get("per_coin", {}),
            coin,
            base_capital=session.get("settings", {}).get("capital_per_coin", 1000),
        )
        data["totals"]["per_coin"] = per_coin

        pc["trades"] += 1
        is_short = direction.upper() == "SHORT"
        if is_short:
            pc["short_trades"] = pc.get("short_trades", 0) + 1
        else:
            pc["long_trades"] = pc.get("long_trades", 0) + 1

        _pc_cap = _safe_number(pc.get("capital", 1000), 1000.0)
        _pc_reward = reward_amount if reward_amount is not None else round(_pc_cap * REWARD_PERCENT, 2)
        _pc_risk   = risk_amount   if risk_amount   is not None else round(_pc_cap * RISK_PERCENT,   2)
        if is_win:
            pc["wins"]    += 1
            pc["capital"] = round(_pc_cap + _pc_reward, 2)
        else:
            pc["losses"]  += 1
            pc["capital"] = round(_pc_cap - _pc_risk, 2)
        pc_total = pc["wins"] + pc["losses"]
        pc["win_rate"] = round(pc["wins"] / pc_total * 100) if pc_total > 0 else 0

        save_log(data)
        print(
            f"[PROJECT LOG] [{coin}] Recorded {outcome} — "
            f"all time: {data['totals']['all_time_wins']}W "
            f"{data['totals']['all_time_losses']}L "
            f"({data['totals']['all_time_win_rate']}%)"
        )

    except Exception as e:
        print(f"[PROJECT LOG] Error recording outcome: {e}")


def update_milestone(milestone_name, value=True):
    try:
        data = load_log()
        if not data:
            return
        if milestone_name in data["milestones"]:
            data["milestones"][milestone_name] = value
            save_log(data)
            print(f"[PROJECT LOG] Milestone updated: {milestone_name} = {value}")
    except Exception as e:
        print(f"[PROJECT LOG] Error updating milestone: {e}")


def get_project_stats():
    try:
        data = load_log()
        if not data:
            return None
        return data
    except Exception as e:
        print(f"[PROJECT LOG] Error reading stats: {e}")
        return None
