import json
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

def generate_signal(filtered_indicators, sentiment, history_summary, capital, risk_amount):
    try:
        indicators_text = "\n".join(f"  {k}: {v}" for k, v in filtered_indicators.items())

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system="""You are a disciplined Bitcoin DAY TRADING signal engine.

STRATEGY:
- Target small frequent wins that accumulate into consistent daily profit
- Each trade targets 0.5% to 1.5% price moves — NOT large swings
- Tight stop losses to protect capital — risk only what is specified
- High probability setups only — wait for clear confluence
- One trade at a time — quality over quantity

RULES:
- If confidence is below 70% output Do Not Enter regardless of other factors
- Stop loss and take profit MUST reflect day trading targets (0.5-1.5% moves)
- Stop loss must never exceed the risk amount provided
- Cite specific indicator values in your reasoning
- Never hedge — commit to a clear decision
- Think like a day trader: small wins compound into big gains

Output ONLY valid JSON with no other text:
{
  "signal": "Buy" or "Do Not Enter",
  "confidence": 0-100,
  "entry_price": float or null,
  "stop_loss": float or null,
  "take_profit": float or null,
  "reasoning": {
    "ta_summary": "one sentence on what indicators show",
    "sentiment_summary": "one sentence on news sentiment",
    "history_summary": "one sentence on recent W/L pattern",
    "decision_rationale": "one sentence tying it together"
  }
}""",
            messages=[{
                "role": "user",
                "content": f"""BITCOIN DAY TRADING SIGNAL REQUEST

Selected indicators:
{indicators_text}

News sentiment:  {sentiment.get('news_score', 0.0):+.2f} (range -1.0 to +1.0)
Headlines seen:  {sentiment.get('headline_count', 0)}

Recent W/L pattern: {history_summary}

Account context:
Current capital: ${capital:,.2f}
Max risk this trade: ${risk_amount:.2f} (2% of capital)

Target: small high-probability win (0.5-1.5% move)
Set stop loss so max loss = ${risk_amount:.2f}
Set take profit at 2x the risk distance (2:1 reward:risk)

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
    result = generate_signal(test_indicators, test_sentiment, test_history, 1000.0, 20.0)
    print(json.dumps(result, indent=2))
