import json
import os
import re
import anthropic
from dotenv import load_dotenv
from step_learn import format_learning_for_brain
from config import ENABLE_SHORTS

load_dotenv()
client = anthropic.Anthropic()

_LEARN_KEYWORDS = re.compile(
    r"AVOID|STRONG_AVOID|FAVOR|CAUTION|sentiment threshold|RSI threshold|MACD threshold|"
    r"DI\+\s*gap|learned|reinforcement|pattern|overbought trap|weak trend|strong momentum",
    re.IGNORECASE,
)

# Per-coin price context so the brain sets realistic SL/TP values
_COIN_PRICE_NOTES = {
    "XRP": (
        "IMPORTANT: XRP trades at a very low price (typically $0.50-$3.00). "
        "Stop loss and take profit distances MUST be in CENTS, not dollars - "
        "typical SL distance is $0.01-$0.05, typical TP is $0.015-$0.075. "
        "Never set SL or TP that imply dollar-scale moves for XRP."
    ),
    "ETH":  "ETH trades in the $1,000-$5,000 range. SL/TP distances are typically $5-$50.",
    "SOL":  "SOL trades in the $20-$300 range. SL/TP distances are typically $0.50-$10.",
    "LINK": "LINK trades in the $5-$30 range. SL/TP distances are typically $0.10-$1.50.",
}


def _dne_result(reason: str, coin_name: str) -> dict:
    """Return a well-formed DNE result without calling Sonnet."""
    return {
        "success": True,
        "data": {
            "signal":      "Do Not Enter",
            "confidence":  0,
            "entry_price": None,
            "stop_loss":   None,
            "take_profit": None,
            "reasoning": {
                "ta_summary":         reason,
                "sentiment_summary":  "n/a - pre-gate skip",
                "history_summary":    "n/a - pre-gate skip",
                "decision_rationale": reason,
            },
        },
    }


def _pre_brain_gate(filtered_indicators: dict, coin_name: str) -> str | None:
    """
    Cheap indicator-only checks that guarantee DNE without Sonnet.
    Returns a skip reason string if Sonnet should be skipped, else None.
    Keys in filtered_indicators are lowercase (from step6_filter).
    """
    ind = filtered_indicators or {}

    di_plus  = ind.get("di_plus")  or ind.get("DI_PLUS")
    di_minus = ind.get("di_minus") or ind.get("DI_MINUS")
    rsi      = ind.get("rsi")      or ind.get("RSI")
    bb_width = ind.get("bb_width") or ind.get("BB_WIDTH")

    # Rule 1: DI- > DI+ -> no Buy is possible; with shorts disabled no signal is possible
    if di_plus is not None and di_minus is not None:
        if float(di_minus) > float(di_plus) and not ENABLE_SHORTS:
            return f"DI- {di_minus} > DI+ {di_plus} permanent rule (shorts disabled) -> DNE"

    # Rule 2: RSI < 35 -> no Sell possible; if shorts disabled this only matters when
    # DI- > DI+ already fires above, but catch the short-enabled edge case too
    if rsi is not None and ENABLE_SHORTS:
        if float(rsi) < 35 and di_minus is not None and di_plus is not None:
            if float(di_minus) > float(di_plus):
                return f"RSI {rsi} < 35 + DI- > DI+ -> only valid signal would violate both permanent rules -> DNE"

    # Rule 3: BB_WIDTH squeeze - both Buy and Sell blocked
    if bb_width is not None and float(bb_width) < 0.015:
        return f"BB_WIDTH {bb_width} < 0.015 (squeeze) -> DNE"

    # Rule 4: STRONG_AVOID pattern with assumed max confidence of 85 would still drop below 60
    # Only fire this if confidence after 25pt penalty cannot reach 60 (i.e. pattern always blocks).
    # We assume brain would output at most ~85% confidence; 85 - 25 = 60, so this triggers
    # when the pattern exists AND raw_count >= 5 (penalty not halved by staleness).
    lpath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         f"{coin_name.lower()}_learning.json")
    if os.path.exists(lpath):
        try:
            with open(lpath) as f:
                content = f.read().strip()
            if content:
                ldata = json.loads(content)
                wp = ldata.get("weighted_patterns", [])
                if wp:
                    # Build normalised indicator dict with uppercase keys for _classify_pattern_key
                    norm = {}
                    for k, v in ind.items():
                        norm[k.upper().replace("+", "_PLUS").replace("-", "_MINUS")] = v
                    # Also handle DI_PLUS / DI_MINUS aliases
                    if "DI_PLUS" not in norm and "DI+" in norm:
                        norm["DI_PLUS"] = norm["DI+"]
                    if "DI_MINUS" not in norm and "DI-" in norm:
                        norm["DI_MINUS"] = norm["DI-"]

                    for direction in (["LONG"] if not ENABLE_SHORTS else ["LONG", "SHORT"]):
                        rsi_v    = norm.get("RSI")
                        dip_v    = norm.get("DI_PLUS")
                        dim_v    = norm.get("DI_MINUS")
                        adx_v    = norm.get("ADX")
                        macd_v   = norm.get("MACD")
                        if None in (rsi_v, dip_v, dim_v, adx_v, macd_v):
                            continue
                        rsi_f, dip_f, dim_f, adx_f, macd_f = (
                            float(rsi_v), float(dip_v), float(dim_v),
                            float(adx_v), float(macd_v)
                        )
                        # Skip directions that violate permanent rules - Sonnet would output DNE anyway
                        if direction == "LONG" and dim_f > dip_f:
                            continue
                        if direction == "SHORT" and rsi_f < 35:
                            continue

                        rsi_tag  = "rsi_low" if rsi_f < 40 else ("rsi_high" if rsi_f > 65 else "rsi_mid")
                        gap_tag  = "gap_strong" if abs(dip_f - dim_f) >= 15 else "gap_weak"
                        adx_tag  = "adx_strong" if adx_f >= 27 else "adx_weak"
                        macd_tag = "macd_pos" if macd_f >= 0 else "macd_neg"
                        key = f"{direction}|{rsi_tag}|{gap_tag}|{adx_tag}|{macd_tag}"

                        matched = next((p for p in wp if p.get("key") == key), None)
                        if matched and matched.get("penalty_tag") == "STRONG_AVOID":
                            raw_count = matched.get("raw_count", 0)
                            penalty = 25 if raw_count >= 5 else 12.5
                            # Worst-case: brain outputs 84% confidence (just under our assumed cap)
                            # If even 84 - penalty < 60 then no brain output can survive guardrails
                            if 84 - penalty < 60:
                                return (
                                    f"STRONG_AVOID pattern {key} "
                                    f"(penalty={penalty:.0f}pts, 84-{penalty:.0f}={84-penalty:.0f} < 60) -> DNE"
                                )
        except Exception:
            pass  # learning file unreadable - don't skip, let Sonnet decide

    return None  # no skip condition met


def generate_signal(filtered_indicators, sentiment, history_summary, capital, risk_amount, reward_amount=None, coin_name="ETH", coin_symbol="XETHZUSD"):
    try:
        indicators_text = "\n".join(f"  {k}: {v}" for k, v in filtered_indicators.items())
        coin_price_note = _COIN_PRICE_NOTES.get(coin_name, "")
        if reward_amount is None:
            reward_amount = round(risk_amount * 1.5, 2)

        # ── Pre-brain gate: skip Sonnet when outcome is already determined ────
        skip_reason = _pre_brain_gate(filtered_indicators, coin_name)
        if skip_reason:
            print(f"[BRAIN:{coin_name}] pre-gate skip - {skip_reason} (0 Sonnet calls)")
            return _dne_result(skip_reason, coin_name)

        learning_context = format_learning_for_brain(coin_name)

        # ── [BRAIN] log: learning patterns loaded ────────────────────────────
        try:
            import os, json as _json
            _lpath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  f"{coin_name.lower()}_learning.json")
            _ldata = _json.load(open(_lpath)) if os.path.exists(_lpath) else {}
            _wp = _ldata.get("weighted_patterns", [])
            _pattern_summary = ", ".join(
                f"{p['key']}({p['win_rate_pct']:.0f}%)" for p in _wp[:4]
            ) if _wp else "none"
            print(f"[BRAIN:{coin_name}] learning patterns loaded: {len(_wp)} | "
                  f"top patterns: {_pattern_summary}")
        except Exception:
            print(f"[BRAIN:{coin_name}] learning patterns loaded: 0 (no file)")

        outputs_line = "Two" if not ENABLE_SHORTS else "Three"
        sell_in_outputs = ", Sell (short)," if ENABLE_SHORTS else ""
        if not ENABLE_SHORTS:
            shorts_block = (
                "SHORTING IS DISABLED. Do NOT output a Sell signal under any circumstances.\n"
                "Valid outputs: Buy or Do Not Enter only. Never output Sell."
            )
        else:
            shorts_block = (
                "SELL SIGNAL RULES:\n"
                "A Sell signal means you are shorting the asset - profiting when price goes DOWN.\n"
                "\n"
                "Enter a Sell signal when ALL of these are true:\n"
                "1. DI- is greater than DI+ (confirmed downtrend)\n"
                "2. RSI is between 40 and 65 (not oversold - oversold means the move may already be exhausted)\n"
                "3. MACD is negative or turning negative\n"
                "4. Confidence is 60% or above\n"
                "5. BB_WIDTH is above 0.015 (enough volatility to move)\n"
                "\n"
                "Never enter Sell when:\n"
                "- RSI is below 35 (already oversold, move exhausted)\n"
                "- DI+ is greater than DI- (uptrend, wrong direction)\n"
                "- Sentiment is above +0.5 (strong positive news could reverse the downtrend)\n"
                "\n"
                "For Sell signals calculate SL and TP in reverse:\n"
                "- Stop Loss is ABOVE entry price (price goes up = loss)\n"
                "- Take Profit is BELOW entry price (price goes down = win)\n"
                "- Same 1.5:1 reward:risk ratio applies\n"
                "- SL distance = ATR x 1.5\n"
                "- TP distance = SL distance x 1.5 (below entry)"
            )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=f"""You are analyzing {coin_name} ({coin_symbol}) for a day trading signal. Adjust your analysis for this specific asset. XRP prices are in cents range. ETH SOL LINK prices are in dollars range.

You are an aggressive {coin_name} DAY TRADING signal engine.

STRATEGY:
- Target small frequent wins that accumulate into consistent daily profit
- Each trade targets 0.5% to 2% price moves - capture momentum moves decisively
- Tight stop losses to protect capital - risk only what is specified
- Risk per trade is 2% of current coin capital. Reward target is 3% of current coin capital (1.5:1 reward:risk)
- Position size is calculated as: risk_amount / SL_distance - this determines how many units to buy/sell
- Be aggressive on confluence signals - do not wait for perfection
- 60% confidence is acceptable for entry when indicators align clearly

RULES:
- {outputs_line} possible outputs: Buy (long){sell_in_outputs} or Do Not Enter
- If confidence is below 60% output Do Not Enter regardless of other factors
- Stop loss and take profit MUST reflect day trading targets (0.5-2% moves)
- Take profit must always be at least 1.5x the stop loss distance
- Stop loss must never exceed the risk amount provided
- Cite specific indicator values in your reasoning
- Never hedge - commit to a clear decision
- Think like a day trader: small wins compound into big gains
- When RSI is below 35 (oversold), only enter Buy if sentiment score is above +0.4. Oversold RSI without sentiment confirmation is a falling knife not a bounce setup.
- DI+ above DI- confirms uptrend. DI- above DI+ confirms downtrend. Never enter a Buy signal when DI- is greater than DI+ regardless of what other indicators show.

INDICATOR GUIDANCE:
- ADX measures trend STRENGTH only - it is non-directional. Do NOT use ADX alone as bullish confirmation.
- Use DI+ vs DI- for directional bias when ADX is selected; DI+ > DI- = bullish trend, DI- > DI+ = bearish
- BB_WIDTH measures Bollinger Band squeeze/expansion: BB_WIDTH > 0.02 indicates meaningful price movement is occurring
- BB_WIDTH expanding = volatility increasing, good for momentum entries
- BB_WIDTH < 0.015 = squeeze / low volatility = avoid or wait for breakout
- Prefer BB_WIDTH over ADX for trend confirmation when trend direction is unclear

PRICE SCALE FOR {coin_name}:
{coin_price_note}
{shorts_block}

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

{learning_context if learning_context else "(No reinforcement learning data yet - insufficient trade history)"}

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

        # ── [BRAIN] log: raw confidence + rationale citation scan ────────────
        raw_conf = result.get("confidence", 0)
        rationale = result.get("reasoning", {}).get("decision_rationale", "")
        ta_summary = result.get("reasoning", {}).get("ta_summary", "")
        full_text = rationale + " " + ta_summary
        cited_keywords = _LEARN_KEYWORDS.findall(full_text)
        cited_str = ", ".join(dict.fromkeys(cited_keywords)) if cited_keywords else "none"
        print(f"[BRAIN:{coin_name}] raw confidence {raw_conf}% | signal: {result.get('signal')} | "
              f"rationale cited: {cited_str}")

        # ── POST-PARSE SHORT BLOCK: second line of defence ────────────────────
        if result.get("signal") == "Sell" and not ENABLE_SHORTS:
            print(f"[BRAIN:{coin_name}] POST-PARSE SHORT BLOCK — Sonnet returned Sell despite disable flag — overriding to DNE")
            result["signal"] = "Do Not Enter"
            result["entry_price"] = None
            result["stop_loss"] = None
            result["take_profit"] = None
            if "reasoning" in result:
                result["reasoning"]["decision_rationale"] = (
                    result["reasoning"].get("decision_rationale", "") +
                    " [OVERRIDDEN: POST-PARSE SHORT BLOCK — ENABLE_SHORTS=False]"
                )

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


ATR_STOP_MULTIPLIER = 1.5


def apply_atr_stops(signal_result, filtered_indicators):
    """
    Override brain-generated SL/TP with ATR-based sizing after a Buy or Sell signal.
    Falls back to the brain's values if ATR is unavailable.
    filtered_indicators is the dict from step6 (keys like 'atr', 'ATR', etc.).
    """
    signal = signal_result.get("data", {})
    if signal.get("signal") not in ("Buy", "Sell"):
        return signal_result

    # Resolve ATR from filtered_indicators (keys may be lower or upper case)
    atr = None
    for k, v in (filtered_indicators or {}).items():
        if k.strip().upper() == "ATR":
            try:
                atr = float(v)
            except (TypeError, ValueError):
                pass
            break

    if not atr:
        print(f"[ATR_STOPS] fallback - ATR not found in indicators, using brain SL/TP")
        return signal_result

    entry = signal.get("entry_price")
    if not entry:
        return signal_result
    try:
        entry = float(entry)
    except (TypeError, ValueError):
        return signal_result

    stop_distance = round(atr * ATR_STOP_MULTIPLIER, 4)
    tp_distance   = round(stop_distance * 1.5, 4)

    is_long = signal["signal"] == "Buy"
    if is_long:
        signal["stop_loss"]   = round(entry - stop_distance, 4)
        signal["take_profit"] = round(entry + tp_distance, 4)
    else:
        signal["stop_loss"]   = round(entry + stop_distance, 4)
        signal["take_profit"] = round(entry - tp_distance, 4)

    print(f"[ATR_STOPS] ATR={atr} stop_dist={stop_distance} SL={signal['stop_loss']} TP={signal['take_profit']}")
    signal_result["data"] = signal
    return signal_result


if __name__ == "__main__":
    test_indicators = {"rsi": 42.3, "macd": 15.4, "atr": 180.0, "close": 68500.0}
    test_sentiment  = {"news_score": 0.4, "headline_count": 15}
    test_history    = "3 recent wins on bullish MACD crossovers in uptrend"
    result = generate_signal(test_indicators, test_sentiment, test_history, 1000.0, 20.0, coin_name="ETH")
    print(json.dumps(result, indent=2))
