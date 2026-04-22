def apply_guardrails(signal_result, filtered_indicators=None):
    signal = signal_result["data"]
    overrides = []

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
    return {"success": True, "data": signal, "overrides": overrides}