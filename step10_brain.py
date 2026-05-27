import json
import anthropic
from dotenv import load_dotenv
from step_learn import format_learning_for_brain
from config import ENABLE_SHORTS, CONFIDENCE_THRESHOLD, ATR_MULTIPLIER_STOP, ATR_MULTIPLIER_TP, PER_COIN_LIVE_CONFIG

# Kept for backward compat — experiment/variant_brain.py reads this
_COIN_PRICE_NOTES = {
    "XRP":  "XRP trades at $0.50–$3.00. SL/TP distances are in cents, not dollars.",
    "ETH":  "ETH trades at $1,000–$5,000. SL/TP distances are typically $5–$50.",
    "SOL":  "SOL trades at $20–$300. SL/TP distances are typically $0.50–$10.",
    "LINK": "LINK trades at $5–$30. SL/TP distances are typically $0.10–$1.50.",
}

load_dotenv()
client = anthropic.Anthropic()


def format_momentum_context(ctx: dict) -> str:
    lines = []
    lines.append(f"RSI direction:  {ctx['rsi_6']} (6 candles) / {ctx['rsi_15']} (15 candles)")
    lines.append(f"ADX direction:  {ctx['adx_6']} (6 candles) — MACD histogram {ctx['macd_hist_accel']}")
    lines.append(f"DI status:      {ctx['di_trend']}")
    if ctx.get("di_crossover"):
        lines.append(f"Crossover:      {ctx['di_crossover']}")
    lines.append(f"Session pos:    {ctx['price_vs_session']} ({ctx['session_position_pct']}% of session range)")
    lines.append(f"Session range:  ${ctx['session_low']} – ${ctx['session_high']}")
    lines.append(f"Volume:         {ctx['volume_context']}")
    return "\n".join(lines)


def build_prompts(
    all_indicators: dict,
    sentiment: dict,
    raw_history: list,
    capital: float,
    risk_amount: float,
    reward_amount: float,
    pre_sizing: dict,
    coin_name: str = "ETH",
    coin_symbol: str = "XETHZUSD",
    advisor_note: str = None,
    learning_override: str = None,
) -> tuple[str, str]:
    """
    Build (system_prompt, user_message) for the brain.
    Shared by Sonnet (variants A, C) and GPT (variant B) so the experiment
    isolates the model swap from any prompt drift.
    """
    mom_ctx = all_indicators.get("momentum_context")
    mom_section = ""
    if mom_ctx and isinstance(mom_ctx, dict):
        mom_section = format_momentum_context(mom_ctx)
        print(f"[BRAIN:{coin_name}] momentum: RSI {mom_ctx.get('rsi_6')}/{mom_ctx.get('rsi_15')} "
              f"| MACD {mom_ctx.get('macd_hist_accel')} | pos {mom_ctx.get('price_vs_session')}")

    indicators_text = "\n".join(
        f"  {k}: {v}"
        for k, v in all_indicators.items()
        if k != "momentum_context"
    )

    learning_context = (
        learning_override if learning_override is not None
        else format_learning_for_brain(coin_name)
    )
    try:
        import os, json as _j
        _lp   = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             f"{coin_name.lower()}_learning.json")
        _ld   = _j.load(open(_lp)) if os.path.exists(_lp) else {}
        _wp   = _ld.get("weighted_patterns", [])
        _psum = ", ".join(f"{p['key']}({p['win_rate_pct']:.0f}%)" for p in _wp[:4]) if _wp else "none"
        print(f"[BRAIN:{coin_name}] patterns loaded: {len(_wp)} | top: {_psum}")
    except Exception:
        print(f"[BRAIN:{coin_name}] patterns loaded: 0")

    history_lines = []
    for r in (raw_history or []):
        sig = r.get("signal", "")
        if sig not in ("Buy", "Sell"):
            continue
        direction = "LONG" if sig == "Buy" else "SHORT"
        outcome   = r.get("outcome", "pending")
        if outcome not in ("W", "L"):
            continue
        conf = r.get("confidence", "?")
        ts   = str(r.get("timestamp", ""))[:10]
        ind_str = r.get("indicators", "")
        parsed  = {}
        for part in str(ind_str).split("|"):
            if ":" in part:
                k, _, v = part.partition(":")
                try:
                    parsed[k.strip().upper()] = float(v.strip())
                except ValueError:
                    pass
        def _fmt(key, aliases=()):
            val = parsed.get(key)
            for a in aliases:
                if val is None:
                    val = parsed.get(a)
            return f"{val:.1f}" if isinstance(val, float) else "?"
        rsi_s = _fmt("RSI")
        adx_s = _fmt("ADX")
        dip_s = _fmt("DI_PLUS",  ("DI+",))
        dim_s = _fmt("DI_MINUS", ("DI-",))
        history_lines.append(
            f"  {ts} {direction:5} {outcome} conf:{conf} "
            f"RSI:{rsi_s} ADX:{adx_s} DI+:{dip_s} DI-:{dim_s}"
        )
    history_table = "\n".join(history_lines) if history_lines else "  (no closed trades yet)"

    advisor_block = ""
    if advisor_note:
        advisor_block = f"--- ADVISOR PRE-OPINION ---\n{advisor_note}\n\n"

    if pre_sizing:
        sl_mult = pre_sizing.get("sl_mult", 1.5)
        tp_mult = pre_sizing.get("tp_mult", 2.0)
        sizing_block = (
            f"  If Buy:  Entry ~${pre_sizing['entry']:,.4f} | "
            f"SL ${pre_sizing['long_sl']:,.4f} | TP ${pre_sizing['long_tp']:,.4f}\n"
            f"  If Sell: Entry ~${pre_sizing['entry']:,.4f} | "
            f"SL ${pre_sizing['short_sl']:,.4f} | TP ${pre_sizing['short_tp']:,.4f}\n"
            f"  ATR = {pre_sizing['atr']:.4f} | "
            f"SL dist = {pre_sizing['sl_dist']:.4f} ({sl_mult}×ATR) | "
            f"TP dist = {pre_sizing['tp_dist']:.4f} ({tp_mult}×ATR)\n"
            f"  Reward:Risk = {pre_sizing['rr']:.2f} | "
            f"Required WR to break even = {pre_sizing['breakeven_wr']:.1f}%"
        )
    else:
        sizing_block = "  (ATR sizing unavailable — use judgment on SL distance)"

    outputs_line    = "Two" if not ENABLE_SHORTS else "Three"
    sell_in_outputs = ", Sell (short)," if ENABLE_SHORTS else ""
    if not ENABLE_SHORTS:
        shorts_block = (
            "SHORTING IS DISABLED. Do NOT output Sell under any circumstances.\n"
            "Valid outputs: Buy or Do Not Enter only."
        )
    else:
        shorts_block = (
            "SELL (SHORT) RULES:\n"
            "Enter Sell when ALL of these are true:\n"
            "1. DI- > DI+ (confirmed downtrend)\n"
            "2. RSI between 40 and 65 (not oversold — oversold move is exhausted)\n"
            "3. MACD negative or turning negative\n"
            "4. Confidence ≥ 65%\n"
            "5. BB_WIDTH > 0.008 (enough volatility)\n"
            "\n"
            "Never enter Sell when:\n"
            "- RSI < 35 (already oversold)\n"
            "- DI+ > DI- (uptrend)\n"
            "- Sentiment score > +0.5 (strong positive news can reverse downtrend)"
        )

    system_prompt = f"""You are a {coin_name} scalping signal engine on 3-minute candles.

STRATEGY:
- Scalping: targeting 0.3–0.8% directional moves, resolving within 45–90 minutes.
- Enter only when momentum is clearly established in the current candle sequence.
- Prefer entries: MACD histogram accelerating, DI gap widening.
- Favour longs in lower/mid session range, shorts in upper/mid session range.
- A DI crossover within the last 5 candles is a strong early-trend signal.

RULES:
- {outputs_line} valid outputs: Buy (long){sell_in_outputs} or Do Not Enter.
- Output Do Not Enter when confidence is below {CONFIDENCE_THRESHOLD}%.
- Confidence guidance: {CONFIDENCE_THRESHOLD}–70% for most valid setups; above 70% only when all indicators strongly align. Be conservative.
- Cite specific indicator values in your reasoning.
- Never hedge — one clear decision.

INDICATOR GUIDANCE:
- ADX measures trend STRENGTH only — it is non-directional. Do NOT use ADX as bullish/bearish evidence.
- DI+ > DI- = uptrend (Buy territory). DI- > DI+ = downtrend (Sell territory).
- BB_WIDTH > 0.015 = meaningful volatility. BB_WIDTH < 0.008 = squeeze, avoid.
- MACD histogram acceleration (increasing absolute value) is stronger than MACD level alone.
- Session position: prefer longs in lower/middle third, shorts in upper/middle third.

PERMANENT RULES:
1. Never Buy when DI- > DI+
2. Never Sell when RSI < 35

{shorts_block}

STOP LOSS / TAKE PROFIT:
The system pre-computes SL and TP from ATR — you do NOT set them.
Your job is to decide direction and confidence only.
Do NOT include entry_price, stop_loss, or take_profit in your output.

Output ONLY valid JSON with no other text:
{{
  "signal": "Buy" or "Sell" or "Do Not Enter",
  "confidence": 0-100,
  "reasoning": {{
    "ta_summary":         "one sentence on what the indicators show",
    "sentiment_summary":  "one sentence on news sentiment",
    "history_summary":    "one sentence on the recent W/L pattern",
    "decision_rationale": "one sentence tying it all together"
  }}
}}"""

    user_message = f"""{coin_name} SCALPING SIGNAL REQUEST

--- INDICATORS (all computed) ---
{indicators_text}

--- SHORT-TERM MOMENTUM (last 4 hours of 3-min bars) ---
{mom_section if mom_section else "(not available)"}

--- TRADE GEOMETRY (system pre-computed — do not override) ---
{sizing_block}

--- NEWS SENTIMENT ---
Score:     {sentiment.get('news_score', 0.0):+.2f}  (range -1.0 to +1.0)
Headlines: {sentiment.get('headline_count', 0)}

--- RECENT CLOSED TRADES (last {len(history_lines)}) ---
{history_table}

--- REINFORCEMENT LEARNINGS ---
{learning_context if learning_context else "(no learning data yet — insufficient trade history)"}

--- ACCOUNT CONTEXT ---
Capital:      ${capital:,.2f}
Risk/trade:   ${risk_amount:.2f}  (1.5% of capital)
Reward target:${reward_amount:.2f}  (2.0% of capital, {pre_sizing['rr'] if pre_sizing else 1.33:.2f}:1)

PERMANENT RULES:
1. Never Buy when DI- > DI+
2. Never Sell when RSI < 35

{advisor_block}Generate scalping signal now."""

    return system_prompt, user_message


def parse_brain_response(raw: str, coin_name: str = "ETH") -> dict:
    """
    Parse raw JSON text from the brain (Sonnet or GPT), strip any SL/TP
    fields, and apply the post-parse short block. Returns the same shape
    as generate_signal() — {success, data: {...}}.
    """
    raw = (raw or "").strip()
    clean = raw.removeprefix("```json").removesuffix("```").strip()
    result = json.loads(clean)

    for field in ("entry_price", "stop_loss", "take_profit"):
        result.pop(field, None)

    raw_conf = result.get("confidence", 0)
    rationale = result.get("reasoning", {}).get("decision_rationale", "")
    print(f"[BRAIN:{coin_name}] signal={result.get('signal')} conf={raw_conf}% | {rationale[:80]}")

    if result.get("signal") == "Sell" and not ENABLE_SHORTS:
        print(f"[BRAIN:{coin_name}] POST-PARSE SHORT BLOCK — overriding Sell → DNE")
        result["signal"] = "Do Not Enter"
        result.setdefault("reasoning", {})["decision_rationale"] = (
            result["reasoning"].get("decision_rationale", "") +
            " [OVERRIDDEN: ENABLE_SHORTS=False]"
        )

    return {"success": True, "data": result}


def _brain_error_response(err: Exception) -> dict:
    return {
        "success": False,
        "error":   str(err),
        "data": {
            "signal":     "Do Not Enter",
            "confidence": 0,
            "reasoning": {
                "ta_summary":         "error",
                "sentiment_summary":  "error",
                "history_summary":    "error",
                "decision_rationale": f"Brain failed: {err}",
            },
        },
    }


def generate_signal(
    all_indicators: dict,
    sentiment: dict,
    raw_history: list,
    capital: float,
    risk_amount: float,
    reward_amount: float,
    pre_sizing: dict,
    coin_name: str = "ETH",
    coin_symbol: str = "XETHZUSD",
    advisor_note: str = None,
    learning_override: str = None,
) -> dict:
    """
    Generate a Buy / Sell / Do Not Enter signal using Sonnet.
    """
    try:
        system_prompt, user_message = build_prompts(
            all_indicators    = all_indicators,
            sentiment         = sentiment,
            raw_history       = raw_history,
            capital           = capital,
            risk_amount       = risk_amount,
            reward_amount     = reward_amount,
            pre_sizing        = pre_sizing,
            coin_name         = coin_name,
            coin_symbol       = coin_symbol,
            advisor_note      = advisor_note,
            learning_override = learning_override,
        )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )

        usage = getattr(response, "usage", None)
        if usage is not None:
            cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
            cr = getattr(usage, "cache_read_input_tokens", 0) or 0
            print(
                f"[BRAIN:{coin_name}] tokens in={usage.input_tokens} "
                f"out={usage.output_tokens} cache_write={cw} cache_read={cr}"
            )

        return parse_brain_response(response.content[0].text, coin_name)

    except Exception as e:
        print(f"[ERROR] Brain failed: {e}")
        return _brain_error_response(e)


# ── Backward-compat shim — used by experiment/runner.py ──────────────────────
def apply_atr_stops(signal_result: dict, filtered_indicators: dict, coin_name: str = "ETH") -> dict:
    """
    Legacy function kept so experiment/runner.py does not break.
    In the new pipeline, sizing is computed before the brain call via
    compute_atr_sizing() in step9_gate.py. This shim replicates the old
    post-brain override behaviour for callers that haven't been updated yet.
    """
    signal = signal_result.get("data", {})
    if signal.get("signal") not in ("Buy", "Sell"):
        return signal_result

    atr   = None
    entry = signal.get("entry_price")
    for k, v in (filtered_indicators or {}).items():
        if k.strip().upper() == "ATR":
            try:
                atr = float(v)
            except (TypeError, ValueError):
                pass
            break

    if not atr or not entry:
        return signal_result

    try:
        entry = float(entry)
    except (TypeError, ValueError):
        return signal_result

    coin_cfg   = PER_COIN_LIVE_CONFIG.get(coin_name, {})
    sl_mult    = coin_cfg.get("ATR_SL_MULTIPLIER", ATR_MULTIPLIER_STOP)
    tp_mult    = coin_cfg.get("ATR_TP_MULTIPLIER", ATR_MULTIPLIER_TP)
    sl_dist    = round(atr * sl_mult, 4)
    tp_dist    = round(atr * tp_mult, 4)
    is_long    = signal["signal"] == "Buy"
    signal["stop_loss"]   = round(entry - sl_dist if is_long else entry + sl_dist, 4)
    signal["take_profit"] = round(entry + tp_dist if is_long else entry - tp_dist, 4)
    print(f"[ATR_STOPS:{coin_name}] ATR={atr} sl×{sl_mult}={sl_dist} tp×{tp_mult}={tp_dist} "
          f"SL={signal['stop_loss']} TP={signal['take_profit']}")
    signal_result["data"] = signal
    return signal_result
