import os
import json


def _parse_indicators_str(indicator_str: str) -> dict:
    result = {}
    if not indicator_str:
        return result
    for part in str(indicator_str).split("|"):
        part = part.strip()
        if ":" in part:
            key, _, val = part.partition(":")
            try:
                result[key.strip()] = float(val.strip())
            except ValueError:
                pass
    if "DI_PLUS" not in result and "DI+" in result:
        result["DI_PLUS"] = result["DI+"]
    if "DI_MINUS" not in result and "DI-" in result:
        result["DI_MINUS"] = result["DI-"]
    return result


def _classify_pattern_key(direction: str, ind: dict):
    rsi     = ind.get("RSI")
    di_plus = ind.get("DI_PLUS")
    di_minus= ind.get("DI_MINUS")
    adx     = ind.get("ADX")
    macd    = ind.get("MACD")

    if None in (rsi, di_plus, di_minus, adx, macd):
        return None

    dir_tag = "LONG" if direction in ("LONG", "Buy") else "SHORT" if direction in ("SHORT", "Sell") else None
    if dir_tag is None:
        return None

    rsi_tag = "rsi_low" if rsi < 40 else ("rsi_high" if rsi > 65 else "rsi_mid")
    gap_tag = "gap_strong" if abs(di_plus - di_minus) >= 15 else "gap_weak"
    adx_tag = "adx_strong" if adx >= 27 else "adx_weak"
    macd_tag = "macd_pos" if macd >= 0 else "macd_neg"

    return f"{dir_tag}|{rsi_tag}|{gap_tag}|{adx_tag}|{macd_tag}"


def _load_learning(coin: str) -> dict:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, f"{coin.lower()}_learning.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def apply_guardrails(signal_result, filtered_indicators=None):
    signal = signal_result["data"]
    overrides = []
    guardrail_log = []

    # ── Shared: confidence check ─────────────────────────────────────────────
    if signal["confidence"] < 60:
        overrides.append(f"Confidence {signal['confidence']}% below 60% threshold")
        signal["signal"] = "Do Not Enter"
        signal["entry_price"] = None
        signal["stop_loss"] = None
        signal["take_profit"] = None

    # ── Sell-specific guardrails ─────────────────────────────────────────────
    if signal["signal"] == "Sell":
        ind = filtered_indicators or {}
        rsi   = ind.get("rsi")
        di_pos = ind.get("di_plus") or ind.get("DI+") or ind.get("di+")
        di_neg = ind.get("di_minus") or ind.get("DI-") or ind.get("di-")

        # Block: oversold RSI (move already exhausted)
        if rsi is not None and float(rsi) < 35:
            overrides.append(f"Sell blocked: RSI {rsi} < 35 (oversold, move exhausted)")
            signal["signal"] = "Do Not Enter"
            signal["entry_price"] = None
            signal["stop_loss"] = None
            signal["take_profit"] = None

        # Block: uptrend (wrong direction for short)
        if signal["signal"] == "Sell" and di_pos is not None and di_neg is not None:
            if float(di_pos) > float(di_neg):
                overrides.append(f"Sell blocked: DI+ {di_pos} > DI- {di_neg} (uptrend, wrong direction)")
                signal["signal"] = "Do Not Enter"
                signal["entry_price"] = None
                signal["stop_loss"] = None
                signal["take_profit"] = None

        # Block: SL/TP inverted for a short
        if signal["signal"] == "Sell":
            ep = signal.get("entry_price")
            tp = signal.get("take_profit")
            sl = signal.get("stop_loss")
            if ep and tp and tp >= ep:
                overrides.append(f"Sell TP ${tp} is not below entry ${ep} — invalid short setup")
                signal["signal"] = "Do Not Enter"
                signal["entry_price"] = None
                signal["stop_loss"] = None
                signal["take_profit"] = None
            elif ep and sl and sl <= ep:
                overrides.append(f"Sell SL ${sl} is not above entry ${ep} — invalid short setup")
                signal["signal"] = "Do Not Enter"
                signal["entry_price"] = None
                signal["stop_loss"] = None
                signal["take_profit"] = None

    if overrides:
        signal["reasoning"]["decision_rationale"] += " [OVERRIDDEN: " + " | ".join(overrides) + "]"

    # ── Dynamic confidence decay (permanent rules must pass first) ───────────
    if signal["signal"] in ("Buy", "Sell"):
        coin = str(signal.get("coin", signal.get("symbol", ""))).upper().replace("USDT", "").replace("-", "")
        learning = _load_learning(coin) if coin else {}

        if learning:
            indicators_str = signal.get("indicators", "")
            if not indicators_str and filtered_indicators:
                # Reconstruct from filtered_indicators dict if needed
                indicators_str = "|".join(
                    f"{k}:{v}" for k, v in (filtered_indicators or {}).items()
                )
            ind = _parse_indicators_str(indicators_str)

            direction = "LONG" if signal["signal"] == "Buy" else "SHORT"
            pattern_key = _classify_pattern_key(direction, ind)
            guardrail_log.append(f"pattern_key={pattern_key}")

            if pattern_key:
                weighted_patterns = learning.get("weighted_patterns", [])
                matched = next((p for p in weighted_patterns if p.get("key") == pattern_key), None)

                if matched:
                    penalty = matched.get("confidence_penalty", 0)
                    raw_count = matched.get("raw_count", 0)
                    guardrail_log.append(
                        f"pattern_matched: key={pattern_key} penalty={penalty} "
                        f"raw_count={raw_count} wr={matched.get('weighted_win_rate')}"
                    )

                    # Staleness check 1: too few raw trades
                    if raw_count < 5:
                        penalty = penalty / 2
                        guardrail_log.append(
                            f"staleness_halve: raw_count={raw_count} < 5, penalty halved to {penalty}"
                        )

                    # Staleness check 2: regime drift
                    regime = learning.get("regime", {})
                    avg_adx = regime.get("avg_adx")
                    avg_rsi = regime.get("avg_rsi")
                    current_adx = ind.get("ADX")
                    current_rsi = ind.get("RSI")

                    regime_stale = False
                    if avg_adx and current_adx:
                        if abs(current_adx - avg_adx) / max(abs(avg_adx), 1e-9) > 0.40:
                            regime_stale = True
                            guardrail_log.append(
                                f"regime_drift_adx: current={current_adx:.1f} avg={avg_adx:.1f}"
                            )
                    if avg_rsi and current_rsi:
                        if abs(current_rsi - avg_rsi) / max(abs(avg_rsi), 1e-9) > 0.40:
                            regime_stale = True
                            guardrail_log.append(
                                f"regime_drift_rsi: current={current_rsi:.1f} avg={avg_rsi:.1f}"
                            )
                    if regime_stale:
                        penalty = penalty / 2
                        guardrail_log.append(f"staleness_halve: regime stale, penalty halved to {penalty}")

                    penalty = min(penalty, 30)
                    original_confidence = signal["confidence"]
                    adjusted_confidence = original_confidence - penalty

                    guardrail_log.append(
                        f"confidence_decay: original={original_confidence} "
                        f"penalty={penalty} adjusted={adjusted_confidence}"
                    )

                    if adjusted_confidence < 60:
                        reason = (
                            f"Learning penalty blocked signal: "
                            f"original={original_confidence} penalty={penalty:.1f} "
                            f"adjusted={adjusted_confidence:.1f} < 60 "
                            f"[pattern={pattern_key} tag={matched.get('penalty_tag')}]"
                        )
                        overrides.append(reason)
                        guardrail_log.append(f"BLOCKED: {reason}")
                        signal["signal"] = "Do Not Enter"
                        signal["entry_price"] = None
                        signal["stop_loss"] = None
                        signal["take_profit"] = None
                        signal["reasoning"]["decision_rationale"] += " [OVERRIDDEN: " + reason + "]"
                    else:
                        signal["confidence"] = adjusted_confidence
                        guardrail_log.append(
                            f"PASSED: confidence updated to {adjusted_confidence}"
                        )
                else:
                    guardrail_log.append(f"pattern_not_found: no weighted pattern for key={pattern_key}")
            else:
                guardrail_log.append("pattern_key=None: insufficient indicators to classify")
        else:
            guardrail_log.append(f"learning_file_missing_or_empty: coin={coin}")

    signal["guardrail_log"] = guardrail_log
    return {"success": True, "data": signal, "overrides": overrides}
