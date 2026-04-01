def filter_indicators(all_indicators, selection_result):
    selected_keys = selection_result["data"]["selected"]
    filtered = {k: v for k, v in all_indicators.items() if k in selected_keys or k == "close"}
    return {"success": True, "data": filtered, "reason": selection_result["data"]["reason"]}