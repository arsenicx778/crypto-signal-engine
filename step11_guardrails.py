import os
import json
from config import ENABLE_SHORTS, CONFIDENCE_THRESHOLD


def _load_learning(coin: str) -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f"{coin.lower()}_learning.json")
    if not os.path.exists(path):
        return {}
    try:
        if os.path.getsize(path) == 0:
            return {}
        with open(path) as f:
            content = f.read().strip()
        return json.loads(content) if content else {}
    except Exception:
        return {}


def _classify_pattern_key(direction: str, ind: dict) -> str | None:
    rsi     = ind.get("RSI")
    di_plus = ind.get("DI_PLUS")
    di_minus= ind.get("DI_MINUS")
    adx     = ind.get("ADX")
    macd    = ind.get("MACD")

    if None in (rsi, di_plus, di_minus, adx, macd):
        return None

    dir_tag = "LONG" if direction in ("LONG", "Buy") else ("SHORT" if direction in ("SHORT", "Sell") else None)
    if dir_tag is None:
        return None

    rsi_tag  = "rsi_low"    if rsi     < 40 else ("rsi_high"   if rsi     > 65 else "rsi_mid")
    gap_tag  = "gap_strong" if abs(di_plus - di_minus) >= 15   else "gap_weak"
    adx_tag  = "adx_strong" if adx     >= 27                   else "adx_weak"
    macd_tag = "macd_pos"   if macd    >= 0                    else "macd_neg"

    return f"{dir_tag}|{rsi_tag}|{gap_tag}|{adx_tag}|{macd_tag}"


def _parse_indicators_for_learning(all_indicators: dict) -> dict:
    """Convert the all_indicators dict (lowercase keys) to uppercase for _classify_pattern_key."""
    out = {}
    for k, v in (all_indicators or {}).items():
        ku = k.strip().upper().replace("+", "_PLUS").replace("-", "_MINUS")
        try:
            out[ku] = float(v)
        except (TypeError, ValueError):
            pass
    # Normalise DI aliases
    if "DI_PLUS"  not in out and "DI+" in out: out["DI_PLUS"]  = out["DI+"]
    if "DI_MINUS" not in out and "DI-" in out: out["DI_MINUS"] = out["DI-"]
    return out


def _dne(signal: dict, overrides: list, reason: str) -> None:
    overrides.append(reason)
    signal["signal"]      = "Do Not Enter"
    signal["entry_price"] = None
    signal["stop_loss"]   = None
    signal["take_profit"] = None
    signal.setdefault("reasoning", {})["decision_rationale"] = (
        signal.get("reasoning", {}).get("decision_rationale", "") + f" [OVERRIDDEN: {reason}]"
    )


def apply_guardrails(signal_result: dict, all_indicators: dict = None,
                     coin_name: str = "ETH") -> dict:
    """
    Light post-brain enforcement layer.
    Technical checks (RSI/ADX/BB/DI) already ran in technical_hard_gate.
    This layer handles: shorts toggle, confidence threshold, learning penalties,
    and short geometry validation.
    """
    signal    = signal_result["data"]
    overrides = []
    guard_log = []

    direction = ("LONG"  if signal["signal"] == "Buy"
                 else "SHORT" if signal["signal"] == "Sell"
                 else None)

    # ── Rule 1: shorts toggle ─────────────────────────────────────────────────
    if signal["signal"] == "Sell" and not ENABLE_SHORTS:
        print(f"[GUARD:{coin_name}] SHORT blocked — ENABLE_SHORTS=False")
        _dne(signal, overrides, "SHORT blocked — ENABLE_SHORTS=False")
        signal["guardrail_log"] = guard_log
        return {"success": True, "data": signal, "overrides": overrides}

    if signal["signal"] not in ("Buy", "Sell"):
        signal["guardrail_log"] = guard_log
        return {"success": True, "data": signal, "overrides": overrides}

    # ── Rule 2: confidence threshold ─────────────────────────────────────────
    if signal.get("confidence", 0) < CONFIDENCE_THRESHOLD:
        msg = f"Confidence {signal['confidence']}% below {CONFIDENCE_THRESHOLD}% threshold"
        print(f"[GUARD:{coin_name}] {msg}")
        _dne(signal, overrides, msg)
        signal["guardrail_log"] = guard_log
        return {"success": True, "data": signal, "overrides": overrides}

    # ── Rule 3: learning penalty ──────────────────────────────────────────────
    learning = _load_learning(coin_name)
    if learning:
        ind_parsed    = _parse_indicators_for_learning(all_indicators or {})
        pattern_key   = _classify_pattern_key(direction, ind_parsed)
        guard_log.append(f"pattern_key={pattern_key}")
        print(f"[GUARD:{coin_name}] key: {pattern_key or 'UNCLASSIFIABLE'}")

        if pattern_key:
            weighted_patterns = learning.get("weighted_patterns", [])
            matched = next((p for p in weighted_patterns if p.get("key") == pattern_key), None)

            if matched:
                penalty   = matched.get("confidence_penalty", 0)
                raw_count = matched.get("raw_count", 0)
                guard_log.append(
                    f"pattern_matched: key={pattern_key} penalty={penalty} "
                    f"raw_count={raw_count} wr={matched.get('weighted_win_rate')}"
                )

                # Halve for LOW_CONFIDENCE patterns
                if matched.get("confidence_level") == "LOW_CONFIDENCE":
                    penalty /= 2
                    guard_log.append(f"low_confidence_halve: penalty → {penalty}")

                # Skip penalty entirely when sample is too small to be meaningful
                if raw_count < 3:
                    penalty = 0
                    guard_log.append(f"insufficient_sample: raw_count={raw_count} → penalty=0")
                    print(f"[GUARD:{coin_name}] insufficient sample (n={raw_count}) — no penalty applied")
                # Halve for small but observable samples
                elif raw_count < 10:
                    penalty /= 2
                    guard_log.append(f"small_sample_halve: raw_count={raw_count} penalty → {penalty}")

                # Halve when current regime has drifted significantly from when pattern was learned
                regime      = learning.get("regime", {})
                avg_adx     = regime.get("avg_adx")
                avg_rsi     = regime.get("avg_rsi")
                cur_adx     = ind_parsed.get("ADX")
                cur_rsi     = ind_parsed.get("RSI")
                regime_stale = False
                if avg_adx and cur_adx and abs(cur_adx - avg_adx) / max(abs(avg_adx), 1e-9) > 0.40:
                    regime_stale = True
                    guard_log.append(f"regime_drift_adx: cur={cur_adx:.1f} avg={avg_adx:.1f}")
                if avg_rsi and cur_rsi and abs(cur_rsi - avg_rsi) / max(abs(avg_rsi), 1e-9) > 0.40:
                    regime_stale = True
                    guard_log.append(f"regime_drift_rsi: cur={cur_rsi:.1f} avg={avg_rsi:.1f}")
                if regime_stale:
                    penalty /= 2
                    guard_log.append(f"regime_stale_halve: penalty → {penalty}")

                penalty = min(penalty, 30)
                original_conf = signal["confidence"]
                adjusted_conf = original_conf - penalty
                guard_log.append(
                    f"confidence_decay: original={original_conf} penalty={penalty} adjusted={adjusted_conf}"
                )
                tag = matched.get("penalty_tag", "")
                print(f"[GUARD:{coin_name}] {tag} -{penalty:.0f}pts | "
                      f"{original_conf} → {adjusted_conf:.0f}")

                if adjusted_conf < CONFIDENCE_THRESHOLD:
                    reason = (
                        f"Learning penalty: {original_conf} -{penalty:.1f} = {adjusted_conf:.1f} "
                        f"< {CONFIDENCE_THRESHOLD} [pattern={pattern_key} tag={tag}]"
                    )
                    guard_log.append(f"BLOCKED: {reason}")
                    print(f"[GUARD:{coin_name}] BLOCKED — penalty drops below threshold")
                    _dne(signal, overrides, reason)
                else:
                    signal["confidence"] = adjusted_conf
                    guard_log.append(f"PASSED: confidence → {adjusted_conf}")
                    print(f"[GUARD:{coin_name}] PASSED — confidence {adjusted_conf:.0f}% after penalty")
            else:
                guard_log.append(f"no_pattern_match: key={pattern_key}")
                print(f"[GUARD:{coin_name}] no match — confidence unchanged {signal['confidence']}%")
        else:
            guard_log.append("UNCLASSIFIABLE: missing indicators for key")
            print(f"[GUARD:{coin_name}] UNCLASSIFIABLE — no penalty applied")
    else:
        guard_log.append(f"no_learning_file: coin={coin_name}")
        print(f"[GUARD:{coin_name}] no learning file — no penalty")

    # ── Rule 4: short geometry (SL/TP injected before this in main.py) ────────
    # These checks only fire after sizing is injected; with pre-computed ATR
    # geometry they should always pass, but guard against edge cases.
    if signal["signal"] == "Sell":
        ep = signal.get("entry_price")
        tp = signal.get("take_profit")
        sl = signal.get("stop_loss")
        if ep and tp and tp >= ep:
            _dne(signal, overrides, f"Sell TP ${tp} not below entry ${ep} — invalid short geometry")
        elif ep and sl and sl <= ep:
            _dne(signal, overrides, f"Sell SL ${sl} not above entry ${ep} — invalid short geometry")

    signal["guardrail_log"] = guard_log
    return {"success": True, "data": signal, "overrides": overrides}
