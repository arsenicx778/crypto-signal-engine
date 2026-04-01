import json
import csv
import os
import anthropic
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

SIGNALS_FILE = "signals.csv"
MAX_ADJUSTMENTS = 2

def get_open_trade():
    if not os.path.exists(SIGNALS_FILE):
        return None
    try:
        with open(SIGNALS_FILE, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
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

        if headline_count == 0:
            print("[TP] No headlines available — skipping TP adjustment check")
            return None

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system="""You are a day trading take profit adjustment engine.

You are evaluating whether current news sentiment is strong enough
to justify moving the take profit higher on an open Bitcoin trade.

This is a DAY TRADING strategy — small frequent wins are the goal.
Be conservative. Only recommend adjustment on genuinely strong sentiment.
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
Current TP:     ${float(trade.get('take_profit', 0)):,.2f}
Current SL:     ${float(trade.get('stop_loss', 0)):,.2f}
Adjustments so far: {adjustments_so_far} of {MAX_ADJUSTMENTS} max

Current sentiment:
News score:     {news_score} (range -1.0 to +1.0)
Headlines:      {headline_count}

Should the take profit be adjusted upward based on this sentiment?
Only recommend adjustment if sentiment is genuinely strong.
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
    rows = []
    updated = False

    with open(SIGNALS_FILE, "r") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for row in reader:
            if row["timestamp"] == trade_timestamp:
                row["take_profit"]      = new_tp
                row["tp_adjustments"]   = adjustments_used
                log = row.get("tp_adjustment_log", "") or ""
                timestamp = datetime.utcnow().strftime("%H:%M:%S")
                row["tp_adjustment_log"] = f"{log} | [{timestamp}] TP→${new_tp:,.2f}: {reason}".strip(" |")
                updated = True
            rows.append(row)

    # Add new columns if not present
    for col in ["tp_adjustments", "tp_adjustment_log"]:
        if col not in fieldnames:
            fieldnames.append(col)

    with open(SIGNALS_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    if updated:
        print(f"[TP] signals.csv updated — adjustment {adjustments_used}/{MAX_ADJUSTMENTS} applied")

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
