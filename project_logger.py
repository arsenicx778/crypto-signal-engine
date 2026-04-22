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
    with open(PROJECT_LOG, "w") as f:
        json.dump(data, f, indent=2)


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
        _capital_end = session["results"]["capital_end"]
        _reward = reward_amount if reward_amount is not None else round(_capital_end * REWARD_PERCENT, 2)
        _risk   = risk_amount   if risk_amount   is not None else round(_capital_end * RISK_PERCENT,   2)

        session["results"]["trades"] += 1
        if is_win:
            session["results"]["wins"]        += 1
            session["results"]["capital_end"] = round(_capital_end + _reward, 2)
        else:
            session["results"]["losses"]      += 1
            session["results"]["capital_end"] = round(_capital_end - _risk, 2)

        session["results"]["capital_end"] = round(session["results"]["capital_end"])

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

        if session["results"]["capital_end"] > data["totals"]["peak_capital"]:
            data["totals"]["peak_capital"] = session["results"]["capital_end"]

        # Update per_coin breakdown (trades, wins, losses, win_rate, capital)
        per_coin = data["totals"].get("per_coin", {})
        if coin in per_coin:
            pc = per_coin[coin]
            pc["trades"] += 1
            is_short = direction.upper() == "SHORT"
            if is_short:
                pc["short_trades"] = pc.get("short_trades", 0) + 1
            else:
                pc["long_trades"] = pc.get("long_trades", 0) + 1
            _pc_cap = pc.get("capital", 1000)
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
