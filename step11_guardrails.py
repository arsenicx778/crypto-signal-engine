import os
import json
from config import (
    ENABLE_SHORTS,
    CONFIDENCE_THRESHOLD,
    SCALP_RSI_LONG_MAX,
    SCALP_RSI_SHORT_MIN,
    MIN_DI_GAP,
    MIN_BB_WIDTH,
    PER_COIN_LIVE_CONFIG,
)


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

    rsi_tag  = "rsi_low" if rsi < 40 else ("rsi_high" if rsi > 65 else "rsi_mid")
    gap_tag  = "gap_strong" if abs(di_plus - di_minus) >= 15 else "gap_weak"
    adx_tag  = "adx_strong" if adx >= 27 else "adx_weak"
    macd_tag = "macd_pos" if macd >= 0 else "macd_neg"

    return f"{dir_tag}|{rsi_tag}|{gap_tag}|{adx_tag}|{macd_tag}"


def _load_learning(coin: str) -> dict:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, f"{coin.lower()}_learning.json")
    if not os.path.exists(path):
        return {}
    try:
        if os.path.getsize(path) == 0:
            return {}
        with open(path) as f:
            content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)
    except Exception:
        return {}


def _dne(signal, overrides, reason):
    """Mutate signal to Do Not Enter, append reason, return True (blocked)."""
    overrides.append(reason)
    signal["signal"]      = "Do Not Enter"
    signal["entry_price"] = None
    signal["stop_loss"]   = None
    signal["take_profit"] = None
    signal["reasoning"]["decision_rationale"] += f" [OVERRIDDEN: {reason}]"
    return True


def apply_guardrails(signal_result, filtered_indicators=None):
    signal    = signal_result["data"]
    overrides = []
    guardrail_log = []

    coin = str(signal.get("coin", signal.get("symbol", ""))).upper().replace("USDT", "").replace("-", "") or "?"
    direction = "LONG" if signal["signal"] == "Buy" else ("SHORT" if signal["signal"] == "Sell" else None)

    # Resolve live indicator values from filtered_indicators dict (lowercase keys from step6)
    ind_live = filtered_indicators or {}
    rsi      = ind_live.get("rsi")
    di_plus  = ind_live.get("di_plus")  or ind_live.get("DI_PLUS")
    di_minus = ind_live.get("di_minus") or ind_live.get("DI_MINUS")
    adx      = ind_live.get("adx")
    bb_width = ind_live.get("bb_width")
    close    = ind_live.get("close")

    # ── Rule 1: ENABLE_SHORTS ────────────────────────────────────────────────
    if signal["signal"] == "Sell" and not ENABLE_SHORTS:
        print(f"[GUARD:{coin}] SHORT signal blocked — ENABLE_SHORTS=False")
        _dne(signal, overrides, "SHORT signal blocked — ENABLE_SHORTS=False")
        signal["guardrail_log"] = guardrail_log
        return {"success": True, "data": signal, "overrides": overrides}

    if signal["signal"] not in ("Buy", "Sell"):
        signal["guardrail_log"] = guardrail_log
        return {"success": True, "data": signal, "overrides": overrides}

    # ── Rule 2: session filter (placeholder — disabled) ──────────────────────
    # Uncomment to enable session filtering:
    # from datetime import datetime, timezone
    # utc_hour = datetime.now(timezone.utc).hour
    # ACTIVE_SESSIONS = [(13, 22)]  # UTC: NY + London overlap
    # in_session = any(start <= utc_hour < end for start, end in ACTIVE_SESSIONS)
    # if not in_session:
    #     print(f"[GUARD:{coin}] blocked — outside active session hours (UTC {utc_hour:02d}:xx)")
    #     _dne(signal, overrides, f"outside active session hours (UTC {utc_hour:02d}:xx)")

    # ── Rule 3: RSI bounds ───────────────────────────────────────────────────
    if rsi is not None:
        rsi_f = float(rsi)
        if direction == "LONG" and rsi_f > SCALP_RSI_LONG_MAX:
            msg = f"LONG blocked — RSI {rsi_f:.1f} above {SCALP_RSI_LONG_MAX} ceiling"
            print(f"[GUARD:{coin}] {msg}")
            if _dne(signal, overrides, msg):
                signal["guardrail_log"] = guardrail_log
                return {"success": True, "data": signal, "overrides": overrides}
        if direction == "SHORT" and rsi_f < SCALP_RSI_SHORT_MIN:
            msg = f"SHORT blocked — RSI {rsi_f:.1f} below {SCALP_RSI_SHORT_MIN} floor"
            print(f"[GUARD:{coin}] {msg}")
            if _dne(signal, overrides, msg):
                signal["guardrail_log"] = guardrail_log
                return {"success": True, "data": signal, "overrides": overrides}

    # ── Rule 4: DI gap ───────────────────────────────────────────────────────
    if di_plus is not None and di_minus is not None:
        gap = abs(float(di_plus) - float(di_minus))
        if gap < MIN_DI_GAP:
            msg = f"blocked — DI gap {gap:.1f} below minimum {MIN_DI_GAP}"
            print(f"[GUARD:{coin}] {msg}")
            if _dne(signal, overrides, msg):
                signal["guardrail_log"] = guardrail_log
                return {"success": True, "data": signal, "overrides": overrides}

    # ── Rule 5: per-coin ADX window ──────────────────────────────────────────
    if adx is not None:
        adx_f    = float(adx)
        coin_cfg = PER_COIN_LIVE_CONFIG.get(coin, {})
        adx_min  = coin_cfg.get("ADX_MIN", 18)
        adx_max  = coin_cfg.get("ADX_MAX", 50)
        if adx_f < adx_min:
            msg = f"{direction} blocked — ADX {adx_f:.1f} below {adx_min} minimum for {coin}"
            print(f"[GUARD:{coin}] {msg}")
            if _dne(signal, overrides, msg):
                signal["guardrail_log"] = guardrail_log
                return {"success": True, "data": signal, "overrides": overrides}
        if adx_f > adx_max:
            msg = f"{direction} blocked — ADX {adx_f:.1f} above {adx_max} maximum for {coin}"
            print(f"[GUARD:{coin}] {msg}")
            if _dne(signal, overrides, msg):
                signal["guardrail_log"] = guardrail_log
                return {"success": True, "data": signal, "overrides": overrides}

    # ── Rule 6: BB width ─────────────────────────────────────────────────────
    if bb_width is not None:
        bbw = float(bb_width)
        if bbw < MIN_BB_WIDTH:
            msg = f"blocked — BB width {bbw:.4f} below {MIN_BB_WIDTH} minimum"
            print(f"[GUARD:{coin}] {msg}")
            if _dne(signal, overrides, msg):
                signal["guardrail_log"] = guardrail_log
                return {"success": True, "data": signal, "overrides": overrides}

    # ── Rule 7: candle confirmation ──────────────────────────────────────────
    coin_cfg = PER_COIN_LIVE_CONFIG.get(coin, {})
    if coin_cfg.get("REQUIRE_CANDLE_CONFIRMATION", False) and close is not None:
        # We need the previous candle close — it's stored as prev_close in filtered_indicators
        prev_close = ind_live.get("prev_close")
        if prev_close is not None:
            close_f      = float(close)
            prev_close_f = float(prev_close)
            if direction == "LONG" and close_f < prev_close_f:
                msg = "LONG blocked — red candle confirmation required"
                print(f"[GUARD:{coin}] {msg}")
                if _dne(signal, overrides, msg):
                    signal["guardrail_log"] = guardrail_log
                    return {"success": True, "data": signal, "overrides": overrides}
            if direction == "SHORT" and close_f > prev_close_f:
                msg = "SHORT blocked — green candle confirmation required"
                print(f"[GUARD:{coin}] {msg}")
                if _dne(signal, overrides, msg):
                    signal["guardrail_log"] = guardrail_log
                    return {"success": True, "data": signal, "overrides": overrides}

    # ── Rule 8: HTF trend filter ─────────────────────────────────────────────
    htf_trend = signal.get("htf_trend", "").upper()
    if direction == "LONG" and htf_trend == "BEARISH":
        msg = "LONG blocked — daily HTF trend is BEARISH"
        print(f"[GUARD:{coin}] {msg}")
        if _dne(signal, overrides, msg):
            signal["guardrail_log"] = guardrail_log
            return {"success": True, "data": signal, "overrides": overrides}

    # ── Rule 9: confidence threshold ─────────────────────────────────────────
    if signal["confidence"] < CONFIDENCE_THRESHOLD:
        msg = f"Confidence {signal['confidence']}% below {CONFIDENCE_THRESHOLD}% threshold"
        print(f"[GUARD:{coin}] {msg}")
        if _dne(signal, overrides, msg):
            signal["guardrail_log"] = guardrail_log
            return {"success": True, "data": signal, "overrides": overrides}

    # ── Rule 10: learning file penalties ─────────────────────────────────────
    coin = str(signal.get("coin", signal.get("symbol", ""))).upper().replace("USDT", "").replace("-", "") or coin
    learning = _load_learning(coin) if coin else {}

    if learning:
        indicators_str = signal.get("indicators", "")
        if not indicators_str and filtered_indicators:
            indicators_str = "|".join(f"{k}:{v}" for k, v in (filtered_indicators or {}).items())
        ind_parsed = _parse_indicators_str(indicators_str)

        pattern_key = _classify_pattern_key(direction, ind_parsed)
        guardrail_log.append(f"pattern_key={pattern_key}")
        print(f"[GUARD:{coin}] key: {pattern_key or 'UNCLASSIFIABLE (missing indicators)'}")

        if pattern_key:
            weighted_patterns = learning.get("weighted_patterns", [])
            matched = next((p for p in weighted_patterns if p.get("key") == pattern_key), None)

            if matched:
                penalty    = matched.get("confidence_penalty", 0)
                raw_count  = matched.get("raw_count", 0)
                guardrail_log.append(
                    f"pattern_matched: key={pattern_key} penalty={penalty} "
                    f"raw_count={raw_count} wr={matched.get('weighted_win_rate')}"
                )

                # Halve penalty for LOW_CONFIDENCE patterns
                if matched.get("confidence_level") == "LOW_CONFIDENCE":
                    penalty = penalty / 2
                    guardrail_log.append(f"low_confidence_pattern: halving penalty to {penalty}")

                # Halve penalty when sample is too small
                if raw_count < 10:
                    penalty = penalty / 2
                    guardrail_log.append(
                        f"staleness_halve: raw_count={raw_count} < 10, penalty halved to {penalty}"
                    )

                # Halve penalty when regime has drifted significantly
                regime      = learning.get("regime", {})
                avg_adx     = regime.get("avg_adx")
                avg_rsi     = regime.get("avg_rsi")
                current_adx = ind_parsed.get("ADX")
                current_rsi = ind_parsed.get("RSI")

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
                tag = matched.get("penalty_tag", "")
                print(f"[GUARD:{coin}] match: {tag} -{penalty:.0f}pts | "
                      f"{original_confidence} → {adjusted_confidence:.0f}")

                if adjusted_confidence < CONFIDENCE_THRESHOLD:
                    reason = (
                        f"Learning penalty blocked: original={original_confidence} "
                        f"penalty={penalty:.1f} adjusted={adjusted_confidence:.1f} "
                        f"< {CONFIDENCE_THRESHOLD} [pattern={pattern_key} tag={tag}]"
                    )
                    guardrail_log.append(f"BLOCKED: {reason}")
                    print(f"[GUARD:{coin}] BLOCKED below {CONFIDENCE_THRESHOLD} — learning penalty applied")
                    _dne(signal, overrides, reason)
                else:
                    signal["confidence"] = adjusted_confidence
                    guardrail_log.append(f"PASSED: confidence updated to {adjusted_confidence}")
                    print(f"[GUARD:{coin}] PASSED — confidence {adjusted_confidence:.0f}% after penalty")
            else:
                guardrail_log.append(f"pattern_not_found: no weighted pattern for key={pattern_key}")
                print(f"[GUARD:{coin}] no match | confidence unchanged {signal['confidence']} | PASSED")
        else:
            guardrail_log.append("pattern_key=None: insufficient indicators to classify")
            print(f"[GUARD:{coin}] key: UNCLASSIFIABLE — missing indicators, no penalty applied")
    else:
        guardrail_log.append(f"learning_file_missing_or_empty: coin={coin}")
        print(f"[GUARD:{coin}] no learning file — no penalty applied, confidence unchanged")

    # ── Short-specific geometry check ─────────────────────────────────────────
    if signal["signal"] == "Sell":
        ep = signal.get("entry_price")
        tp = signal.get("take_profit")
        sl = signal.get("stop_loss")
        if ep and tp and tp >= ep:
            _dne(signal, overrides, f"Sell TP ${tp} is not below entry ${ep} — invalid short setup")
        elif ep and sl and sl <= ep:
            _dne(signal, overrides, f"Sell SL ${sl} is not above entry ${ep} — invalid short setup")

    signal["guardrail_log"] = guardrail_log
    return {"success": True, "data": signal, "overrides": overrides}
