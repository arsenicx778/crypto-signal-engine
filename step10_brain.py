import json
import anthropic
from dotenv import load_dotenv
from step_learn import format_learning_for_brain

load_dotenv()
client = anthropic.Anthropic()

# Per-coin price context so the brain sets realistic SL/TP values
_COIN_PRICE_NOTES = {
    "XRP": (
        "IMPORTANT: XRP trades at a very low price (typically $0.50–$3.00). "
        "Stop loss and take profit distances MUST be in CENTS, not dollars — "
        "typical SL distance is $0.01–$0.05, typical TP is $0.015–$0.075. "
        "Never set SL or TP that imply dollar-scale moves for XRP."
    ),
    "ETH":  "ETH trades in the $1,000–$5,000 range. SL/TP distances are typically $5–$50.",
    "SOL":  "SOL trades in the $20–$300 range. SL/TP distances are typically $0.50–$10.",
    "AVAX": "AVAX trades in the $10–$100 range. SL/TP distances are typically $0.25–$5.",
}


def generate_signal(filtered_indicators, sentiment, history_summary, capital, risk_amount, reward_amount=None, coin_name="ETH", coin_symbol="XETHZUSD"):
    try:
        indicators_text = "\n".join(f"  {k}: {v}" for k, v in filtered_indicators.items())
        coin_price_note = _COIN_PRICE_NOTES.get(coin_name, "")
        if reward_amount is None:
            reward_amount = round(risk_amount * 1.5, 2)
        learning_context = format_learning_for_brain(coin_name)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=f"""You are analyzing {coin_name} ({coin_symbol}) for a day trading signal. Adjust your analysis for this specific asset. XRP prices are in cents range. ETH SOL AVAX prices are in dollars range.

You are an aggressive {coin_name} DAY TRADING signal engine.

STRATEGY:
- Target small frequent wins that accumulate into consistent daily profit
- Each trade targets 0.5% to 2% price moves — capture momentum moves decisively
- Tight stop losses to protect capital — risk only what is specified
- Risk per trade is 2% of current coin capital. Reward target is 3% of current coin capital (1.5:1 reward:risk)
- Position size is calculated as: risk_amount / SL_distance — this determines how many units to buy/sell
- Be aggressive on confluence signals — do not wait for perfection
- 60% confidence is acceptable for entry when indicators align clearly

RULES:
- Three possible outputs: Buy (long), Sell (short), or Do Not Enter
- If confidence is below 60% output Do Not Enter regardless of other factors
- Stop loss and take profit MUST reflect day trading targets (0.5-2% moves)
- Take profit must always be at least 1.5x the stop loss distance
- Stop loss must never exceed the risk amount provided
- Cite specific indicator values in your reasoning
- Never hedge — commit to a clear decision
- Think like a day trader: small wins compound into big gains
- When RSI is below 35 (oversold), only enter Buy if sentiment score is above +0.4. Oversold RSI without sentiment confirmation is a falling knife not a bounce setup.
- DI+ above DI- confirms uptrend. DI- above DI+ confirms downtrend. Never enter a Buy signal when DI- is greater than DI+ regardless of what other indicators show.

INDICATOR GUIDANCE:
- ADX measures trend STRENGTH only — it is non-directional. Do NOT use ADX alone as bullish confirmation.
- Use DI+ vs DI- for directional bias when ADX is selected; DI+ > DI- = bullish trend, DI- > DI+ = bearish
- BB_WIDTH measures Bollinger Band squeeze/expansion: BB_WIDTH > 0.02 indicates meaningful price movement is occurring
- BB_WIDTH expanding = volatility increasing, good for momentum entries
- BB_WIDTH < 0.015 = squeeze / low volatility = avoid or wait for breakout
- Prefer BB_WIDTH over ADX for trend confirmation when trend direction is unclear

PRICE SCALE FOR {coin_name}:
{coin_price_note}

SELL SIGNAL RULES:
A Sell signal means you are shorting the asset — profiting when price goes DOWN.

Enter a Sell signal when ALL of these are true:
1. DI- is greater than DI+ (confirmed downtrend)
2. RSI is between 40 and 65 (not oversold — oversold means the move may already be exhausted)
3. MACD is negative or turning negative
4. Confidence is 60% or above
5. BB_WIDTH is above 0.015 (enough volatility to move)

Never enter Sell when:
- RSI is below 35 (already oversold, move exhausted)
- DI+ is greater than DI- (uptrend, wrong direction)
- Sentiment is above +0.5 (strong positive news could reverse the downtrend)

For Sell signals calculate SL and TP in reverse:
- Stop Loss is ABOVE entry price (price goes up = loss)
- Take Profit is BELOW entry price (price goes down = win)
- Same 1.5:1 reward:risk ratio applies
- SL distance = ATR × 1.5
- TP distance = SL distance × 1.5 (below entry)

Output ONLY valid JSON with no other text:
{{
  "signal": "Buy" or "Sell" or "Do Not Enter",
  "confidence": 0-100,
  "entry_price": float or null,
  "stop_loss": float or null,
  "take_profit": float or null,
  "reasoning": {{
    "ta_summary": "one sentence on what indicators show",
    "sentiment_summary": "one sentence on news sentiment",
    "history_summary": "one sentence on recent W/L pattern",
    "decision_rationale": "one sentence tying it together"
  }}
}}""",
            messages=[{
                "role": "user",
                "content": f"""{coin_name} DAY TRADING SIGNAL REQUEST

Selected indicators:
{indicators_text}

News sentiment:  {sentiment.get('news_score', 0.0):+.2f} (range -1.0 to +1.0)
Headlines seen:  {sentiment.get('headline_count', 0)}

Recent W/L pattern: {history_summary}

{learning_context if learning_context else "(No reinforcement learning data yet — insufficient trade history)"}

PERMANENT RULES (these override all learnings):
1. Never generate a Buy signal when DI- > DI+
2. Never generate a Sell signal when RSI < 35

Account context:
Current capital: ${capital:,.2f}
Risk this trade: ${risk_amount:.2f} (2% of capital)
Reward target:   ${reward_amount:.2f} (3% of capital, 1.5:1 ratio)

Target: aggressive day trade (0.5-2% move)
Set stop loss so max loss = ${risk_amount:.2f}
Set take profit so max gain = ${reward_amount:.2f} (1.5x stop loss distance)
Position size = risk_amount / SL_distance

Generate day trading signal now."""
            }]
        )

        raw   = response.content[0].text.strip()
        clean = raw.removeprefix("```json").removesuffix("```").strip()
        result = json.loads(clean)
        return {"success": True, "data": result}

    except Exception as e:
        print(f"[ERROR] Brain failed: {e}")
        return {
            "success": False,
            "error":   str(e),
            "data": {
                "signal":      "Do Not Enter",
                "confidence":  0,
                "entry_price": None,
                "stop_loss":   None,
                "take_profit": None,
                "reasoning": {
                    "ta_summary":         "error",
                    "sentiment_summary":  "error",
                    "history_summary":    "error",
                    "decision_rationale": f"Brain failed: {e}"
                }
            }
        }


if __name__ == "__main__":
    test_indicators = {"rsi": 42.3, "macd": 15.4, "atr": 180.0, "close": 68500.0}
    test_sentiment  = {"news_score": 0.4, "headline_count": 15}
    test_history    = "3 recent wins on bullish MACD crossovers in uptrend"
    result = generate_signal(test_indicators, test_sentiment, test_history, 1000.0, 20.0, coin_name="ETH")
    print(json.dumps(result, indent=2))
