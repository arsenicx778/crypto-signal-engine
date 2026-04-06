import json
import anthropic
import requests
from dotenv import load_dotenv
from signal_store import append_signal_row, read_latest_signals
from time_utils import now_pacific_clock

load_dotenv()
client = anthropic.Anthropic()

MAX_ADJUSTMENTS = 2
EXTREME_SENTIMENT_SCORE = 0.75
MIN_HEADLINES_FOR_TP_ADJUST = 3


def get_current_price():
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": "ETHUSD"},
            timeout=5,
        )
        data = r.json()
        return float(list(data["result"].values())[0]["c"][0])
    except Exception:
        return None

def get_open_trade():
    try:
        rows = read_latest_signals()
        open_trades = [
            r for r in rows
            if r.get("signal") == "Buy" and r.get("outcome", "pending") == "pending"
        ]
        return open_trades[-1] if open_trades else None
    except:
        return None

def check_tp_adjustment(trade, sentiment):
    try:
        adjustments_so_far = int(trade.get("tp_adjustments", 0) or 0)

        if adjustments_so_far >= MAX_ADJUSTMENTS:
            print(f"[TP] Max adjustments ({MAX_ADJUSTMENTS}) reached — no further adjustment")
            return None

        news_score    = sentiment.get("news_score", 0.0)
        headline_count = sentiment.get("headline_count", 0)
        entry_price = float(trade.get("entry_price", 0) or 0)
        current_price = get_current_price()

        if not entry_price or current_price is None:
            print("[TP] Current price unavailable — skipping TP adjustment check")
            return None

        if current_price <= entry_price:
            print(
                f"[TP] Trade not in profit yet — current ${current_price:,.2f} vs entry ${entry_price:,.2f}"
            )
            return None

        if headline_count < MIN_HEADLINES_FOR_TP_ADJUST:
            print(f"[TP] Not enough headlines ({headline_count}) — skipping TP adjustment check")
            return None

        if news_score < EXTREME_SENTIMENT_SCORE:
            print(
                f"[TP] Sentiment not extreme enough ({news_score:.2f} < {EXTREME_SENTIMENT_SCORE:.2f})"
            )
            return None

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system="""You are a day trading take profit adjustment engine.

You are evaluating whether current news sentiment is extremely strong enough
to justify moving the take profit higher on an open Ethereum trade.

This is a DAY TRADING strategy — small frequent wins are the goal.
Be conservative. Only recommend adjustment on genuinely extreme sentiment
while the open trade is already in profit.
Adjustments count toward a maximum of 2 per trade.

Output ONLY valid JSON with no other text:
{
  "adjust": true or false,
  "new_take_profit": float or null,
  "reason": "one sentence explanation"
}

If adjust is false, new_take_profit must be null.""",
            messages=[{
                "role": "user",
                "content": f"""Open trade details:
Entry price:    ${float(trade.get('entry_price', 0)):,.2f}
Current price:  ${current_price:,.2f}
Current TP:     ${float(trade.get('take_profit', 0)):,.2f}
Current SL:     ${float(trade.get('stop_loss', 0)):,.2f}
Adjustments so far: {adjustments_so_far} of {MAX_ADJUSTMENTS} max

Current sentiment:
News score:     {news_score} (range -1.0 to +1.0)
Headlines:      {headline_count}

Should the take profit be adjusted upward based on this sentiment?
Only recommend adjustment if sentiment is genuinely extreme and the trade is already winning.
If adjusting, set new TP conservatively — this is day trading, not investing."""
            }]
        )

        raw   = response.content[0].text.strip()
        clean = raw.removeprefix("```json").removesuffix("```").strip()
        result = json.loads(clean)

        if result.get("adjust") and result.get("new_take_profit"):
            new_tp = float(result["new_take_profit"])
            old_tp = float(trade.get("take_profit", 0))

            # Safety check — new TP must be higher than old TP
            if new_tp <= old_tp:
                print(f"[TP] Rejected — new TP ${new_tp:,.2f} not higher than current ${old_tp:,.2f}")
                return None

            print(f"[TP] Adjusting TP: ${old_tp:,.2f} → ${new_tp:,.2f} | {result['reason']}")
            return {
                "new_take_profit":  new_tp,
                "adjustments_used": adjustments_so_far + 1,
                "reason":           result["reason"]
            }
        else:
            print(f"[TP] No adjustment needed: {result.get('reason', 'sentiment not strong enough')}")
            return None

    except Exception as e:
        print(f"[TP] Adjustment check failed: {e}")
        return None

def apply_tp_adjustment(trade_timestamp, new_tp, adjustments_used, reason):
    for row in reversed(read_latest_signals()):
        if row["timestamp"] != trade_timestamp:
            continue
        row["take_profit"] = new_tp
        row["tp_adjustments"] = adjustments_used
        log = row.get("tp_adjustment_log", "") or ""
        timestamp = now_pacific_clock()
        row["tp_adjustment_log"] = f"{log} | [{timestamp}] TP->${new_tp:,.2f}: {reason}".strip(" |")
        append_signal_row(row)
        print(f"[TP] signals.csv appended — adjustment {adjustments_used}/{MAX_ADJUSTMENTS} applied")
        break

def run_tp_adjustment(sentiment):
    trade = get_open_trade()

    if not trade:
        return

    result = check_tp_adjustment(trade, sentiment)

    if result:
        apply_tp_adjustment(
            trade["timestamp"],
            result["new_take_profit"],
            result["adjustments_used"],
            result["reason"]
        )

if __name__ == "__main__":
    test_sentiment = {"news_score": 0.85, "headline_count": 18}
    run_tp_adjustment(test_sentiment)
