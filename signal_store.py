import csv
import os

SIGNALS_FILE = "signals.csv"
FIELDNAMES = [
    "timestamp", "signal", "confidence",
    "entry_price", "stop_loss", "take_profit",
    "outcome", "close_price", "close_time",
    "ta_summary", "sentiment_summary",
    "history_summary", "decision_rationale",
    "overrides", "indicators",
    "tp_adjustments", "tp_adjustment_log",
]
EXTRA_COLUMNS = ["indicators", "tp_adjustments", "tp_adjustment_log"]


def _has_expected_header():
    if not os.path.exists(SIGNALS_FILE):
        return False
    with open(SIGNALS_FILE, newline="", encoding="utf-8") as f:
        first_line = f.readline().strip()
    return first_line.split(",")[:3] == FIELDNAMES[:3]


def _normalize_row(row):
    normalized = dict(row)
    extras = normalized.pop(None, None)
    if extras:
        for i, value in enumerate(extras):
            if i < len(EXTRA_COLUMNS):
                normalized[EXTRA_COLUMNS[i]] = value
    for fieldname in FIELDNAMES:
        normalized.setdefault(fieldname, None)
    return normalized


def read_signal_rows():
    if not os.path.exists(SIGNALS_FILE):
        return []
    with open(SIGNALS_FILE, newline="", encoding="utf-8") as f:
        if _has_expected_header():
            reader = csv.DictReader(f)
        else:
            reader = csv.DictReader(f, fieldnames=FIELDNAMES)
        return [_normalize_row(row) for row in reader]


def read_latest_signals():
    latest_by_timestamp = {}
    order = []
    for row in read_signal_rows():
        timestamp = row.get("timestamp")
        if not timestamp:
            continue
        if timestamp not in latest_by_timestamp:
            order.append(timestamp)
        latest_by_timestamp[timestamp] = row
    return [latest_by_timestamp[timestamp] for timestamp in order]


def append_signal_row(row):
    write_header = not _has_expected_header()
    normalized = {fieldname: row.get(fieldname) for fieldname in FIELDNAMES}

    with open(SIGNALS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(normalized)
