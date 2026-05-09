import json
import anthropic
import requests
from dotenv import load_dotenv
from trade_store import get_trade_store
from time_utils import now_pacific_clock

load_dotenv()
client = anthropic.Anthropic()

MAX_ADJUSTMENTS = 2
EXTREME_SENTIMENT_SCORE = 0.75
MIN_HEADLINES_FOR_TP_ADJUST = 3


def get_current_price(symbol="XETHZUSD"):
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": symbol},
            timeout=5,
        )
        data = r.json()
        return float(list(data["result"].values())[0]["c"][0])
    except Exception:
        return None


def _unpack_trade(trade: dict) -> dict:
    """Unpack metadata JSON into top-level keys."""
    row = dict(trade)
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except Exception:
        meta = {}
    row["tp_adjustments"]   = meta.get("tp_adjustments", 0)
    row["tp_adjustment_log"] = meta.get("tp_adjustment_log", "")
    return row


def get_open_trade(coin_name=None, signals_file=None):
    """Get the most recent open Buy trade for the coin from SQLite."""
    if coin_name is None and signals_file:
        import os
        base = os.path.basename(signals_file or "")
        coin_name = base.replace("_signals.csv", "").upper()
    if not coin_name:
        coin_name = "ETH"
    try:
        store = get_trade_store()
        pending = [
            t for t in store.get_pending_trades(coin=coin_name)
            if t.get("signal") == "Buy"
        ]
        if not pending:
            return None
        return _unpack_trade(pending[-1])
    except:
        return None


def check_tp_adjustment(trade, sentiment, symbol="XETHZUSD"):
    try:
        adjustments_so_far = int(trade.get("tp_adjustments", 0) or 0)

        if adjustments_so_far >= MAX_ADJUSTMENTS:
            print(f"[TP] Max adjustments ({MAX_ADJUSTMENTS}) reached — no further adjustment")
            return None

        news_score     = sentiment.get("news_score", 0.0)
        headline_count = sentiment.get("headline_count", 0)
        entry_price    = float(trade.get("entry_price", 0) or 0)
        current_price  = get_current_price(symbol)

        if not entry_price or current_price is None:
            print("[TP] Current price unavailable — skipping TP adjustment check")
            return None

        if current_price <= entry_price:
            print(
                f"[TP] Trade not in profit yet — current ${current_price:,.4f} vs entry ${entry_price:,.4f}"
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
to justify moving the take profit higher on an open crypto trade.

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
Entry price:    ${float(trade.get('entry_price', 0)):,.4f}
Current price:  ${current_price:,.4f}
Current TP:     ${float(trade.get('take_profit', 0)):,.4f}
Current SL:     ${float(trade.get('stop_loss', 0)):,.4f}
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

            if new_tp <= old_tp:
                print(f"[TP] Rejected — new TP ${new_tp:,.4f} not higher than current ${old_tp:,.4f}")
                return None

            print(f"[TP] Adjusting TP: ${old_tp:,.4f} → ${new_tp:,.4f} | {result['reason']}")
            return {
                "new_take_profit":  new_tp,
                "adjustments_used": adjustments_so_far + 1,
                "reason":           result["reason"],
            }
        else:
            print(f"[TP] No adjustment needed: {result.get('reason', 'sentiment not strong enough')}")
            return None

    except Exception as e:
        print(f"[TP] Adjustment check failed: {e}")
        return None


def apply_tp_adjustment(trade_id, new_tp, adjustments_used, reason):
    """Update the trade's take_profit in SQLite."""
    store = get_trade_store()
    timestamp = now_pacific_clock()
    # Build log string
    trade = None
    with store.get_connection() as conn:
        row = conn.execute("SELECT metadata FROM trades WHERE id=?", (trade_id,)).fetchone()
        if row:
            try:
                meta = json.loads(row[0] or "{}")
            except Exception:
                meta = {}
            old_log = meta.get("tp_adjustment_log", "") or ""
            new_log = f"{old_log} | [{timestamp}] TP->${new_tp:,.4f}: {reason}".strip(" |")
    store.update_trade_tp(trade_id, new_tp, adjustments_used, new_log)
    print(f"[TP] SQLite updated — adjustment {adjustments_used}/{MAX_ADJUSTMENTS} applied")


def run_tp_adjustment(sentiment, coin_name=None, signals_file=None, symbol="XETHZUSD"):
    # coin_name preferred; accept signals_file for backward compat
    if coin_name is None and signals_file:
        import os
        base = os.path.basename(signals_file or "")
        coin_name = base.replace("_signals.csv", "").upper()
    if not coin_name:
        coin_name = "ETH"

    trade = get_open_trade(coin_name=coin_name)

    if not trade:
        return

    result = check_tp_adjustment(trade, sentiment, symbol=symbol)

    if result:
        trade_id = trade.get("id")
        if trade_id:
            apply_tp_adjustment(
                trade_id,
                result["new_take_profit"],
                result["adjustments_used"],
                result["reason"],
            )
        else:
            print("[TP] Warning: trade has no id — cannot update SQLite")


if __name__ == "__main__":
    test_sentiment = {"news_score": 0.85, "headline_count": 18}
    run_tp_adjustment(test_sentiment, coin_name="ETH")
