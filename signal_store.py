import csv
import os

SIGNALS_FILE = "eth_signals.csv"
FIELDNAMES = [
    "timestamp", "signal", "direction", "confidence",
    "entry_price", "stop_loss", "take_profit",
    "risk_amount", "reward_amount",
    "outcome", "close_price", "close_time",
    "ta_summary", "sentiment_summary",
    "history_summary", "decision_rationale",
    "overrides", "indicators",
    "tp_adjustments", "tp_adjustment_log",
]
EXTRA_COLUMNS = ["indicators", "tp_adjustments", "tp_adjustment_log"]


def _has_expected_header(signals_file=None):
    path = signals_file or SIGNALS_FILE
    if not os.path.exists(path):
        return False
    with open(path, newline="", encoding="utf-8") as f:
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


def parse_coin_csv(filepath):
    """
    Parse a coin signals CSV that may contain multiple schema headers mid-file.
    Returns list of normalized dicts compatible with FIELDNAMES.
    """
    trades = []
    if not os.path.exists(filepath):
        return trades

    current_header = None
    with open(filepath, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for parts in reader:
            if not parts:
                continue

            if parts[:2] == ["timestamp", "signal"]:
                current_header = parts
                continue

            if current_header is None or len(parts) < len(current_header):
                continue

            row = dict(zip(current_header, parts[: len(current_header)]))
            if len(parts) > len(current_header):
                row[None] = parts[len(current_header) :]
            row = _normalize_row(row)

            outcome = str(row.get('outcome', '') or '').strip()
            if outcome not in ('W', 'L', 'pending'):
                continue

            try:
                risk = float(row.get('risk_amount', 0) or 0)
            except (ValueError, TypeError):
                risk = 0.0

            try:
                reward = float(row.get('reward_amount', 0) or 0)
            except (ValueError, TypeError):
                reward = 0.0

            if risk == 0 and reward == 0 and outcome in ('W', 'L'):
                risk = 20.0
                reward = 30.0

            direction = str(row.get('direction', '') or '').strip()
            if not direction:
                sig = str(row.get('signal', '') or '').strip()
                if sig == 'Buy':
                    direction = 'LONG'
                elif sig == 'Sell':
                    direction = 'SHORT'

            normalized = {fieldname: None for fieldname in FIELDNAMES}
            normalized.update({
                'timestamp':          str(row.get('timestamp', '') or ''),
                'close_time':         str(row.get('close_time', '') or '').strip(),
                'signal':             str(row.get('signal', '') or '').strip(),
                'direction':          direction,
                'confidence':         str(row.get('confidence', '') or '').strip(),
                'outcome':            outcome,
                'risk_amount':        risk,
                'reward_amount':      reward,
                'entry_price':        row.get('entry_price', '') or '',
                'stop_loss':          row.get('stop_loss', '') or '',
                'take_profit':        row.get('take_profit', '') or '',
                'close_price':        row.get('close_price', '') or '',
                'indicators':         row.get('indicators', '') or '',
                'ta_summary':         row.get('ta_summary', '') or '',
                'sentiment_summary':  row.get('sentiment_summary', '') or '',
                'history_summary':    row.get('history_summary', '') or '',
                'decision_rationale': row.get('decision_rationale', '') or '',
                'overrides':          row.get('overrides', '') or '',
                'tp_adjustments':     row.get('tp_adjustments', '') or '',
                'tp_adjustment_log':  row.get('tp_adjustment_log', '') or '',
            })
            trades.append(normalized)

    return trades


def read_signal_rows(signals_file=None):
    path = signals_file or SIGNALS_FILE
    return parse_coin_csv(path)


def read_latest_signals(signals_file=None):
    # Deduplicate by (timestamp, signal, entry_price) so multiple trades at the
    # same clock second (common when coins run in parallel) are all preserved.
    seen = {}
    order = []
    for row in parse_coin_csv(signals_file or SIGNALS_FILE):
        timestamp = row.get("timestamp")
        if not timestamp:
            continue
        key = (
            timestamp,
            row.get("signal", ""),
            str(row.get("entry_price", "")),
            str(row.get("stop_loss", "")),
        )
        if key not in seen:
            order.append(key)
        seen[key] = row
    return [seen[k] for k in order]


def append_signal_row(row, signals_file=None):
    path = signals_file or SIGNALS_FILE
    write_header = not _has_expected_header(path)
    normalized = {fieldname: row.get(fieldname) for fieldname in FIELDNAMES}

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(normalized)
