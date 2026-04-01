def apply_guardrails(signal_result):
    signal = signal_result["data"]
    overrides = []
    if signal["confidence"] < 70:
        overrides.append(f"Confidence {signal['confidence']}% below 70% threshold")
        signal["signal"] = "Do Not Enter"
        signal["entry_price"] = None
        signal["stop_loss"] = None
        signal["take_profit"] = None
    if overrides:
        signal["reasoning"]["decision_rationale"] += " [OVERRIDDEN: " + " | ".join(overrides) + "]"
    return {"success": True, "data": signal, "overrides": overrides}