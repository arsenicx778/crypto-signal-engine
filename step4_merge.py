def merge_indicators(compute_result):
    if not compute_result["success"]:
        return {"success": False, "error": compute_result["error"], "data": None}
    return {"success": True, "data": compute_result["data"]}