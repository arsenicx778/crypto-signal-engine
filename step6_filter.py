def filter_indicators(all_indicators, selection_result):
    selected_keys = selection_result["data"]["selected"]
    always_pass = {"close", "prev_close"}
    filtered = {k: v for k, v in all_indicators.items() if k in selected_keys or k in always_pass}
    return {"success": True, "data": filtered, "reason": selection_result["data"]["reason"]}