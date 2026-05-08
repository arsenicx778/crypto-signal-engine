# Engine Recovery & SQLite Integration

## Executive Summary

**Problem**: Three trading coins (SOL, LINK, XRP) stopped generating signals at 11:38 AM despite the engine still running. Root cause: stale pending trades from 3+ hours prior were blocking the concurrent trade gates.

**Solution**: 
1. Created SQLite trade database (primary storage)
2. Integrated with existing signal pipeline (CSV still written for backward compat)
3. Recovered 7 stale trades using `fix_stale_trades.py --apply`
4. All coins resumed signal generation immediately

**Status**: ✅ Complete and tested. Engine is now stable with cleaner trade tracking.

---

## Quick Start

### View Engine Health
```bash
python diagnose_engine.py
```
Shows: API status, config flags, file timestamps, cycle counter, open trades, database status.

### View Trading Dashboard
```bash
python dashboard_sqlite.py
```
Shows: Per-coin stats, recent trades, pending positions, aggregate metrics.

### If Coins Stop Generating Signals
```bash
# Inspect stale trades (no changes)
python fix_stale_trades.py

# Apply fix (mark old trades as losses to clear gates)
python fix_stale_trades.py --apply

# Verify recovery
python diagnose_engine.py
```

---

## What Changed

### New Files (5)

| File | Purpose |
|------|---------|
| `trade_store.py` | SQLite database backend — ACID transactions, proper state management |
| `trade_store_integration.py` | Bridge layer between signal pipeline and SQLite |
| `fix_stale_trades.py` | Diagnose & fix trades stuck in "pending" state |
| `diagnose_engine.py` | One-command health check of entire system |
| `dashboard_sqlite.py` | Real-time trading dashboard from SQLite |

### Modified Files (2)

| File | Changes |
|------|---------|
| `step12_output.py` | Now dual-writes to CSV + SQLite; tracks trade IDs |
| `main.py` | Full exception stack traces in error handler (was: error message only) |

### Backward Compatibility
- ✅ CSV files still updated (all existing code works)
- ✅ SQLite runs alongside (can be disabled if needed)
- ✅ No breaking changes to API or signal format

---

## How It Works

### Before (CSV Only)
```
1. Signal → save to CSV
2. Price monitor → updates CSV row (append)
3. Gate reads CSV → counts "pending" rows
4. Problem: Multiple rows per trade, false positive gate blockages
5. Monitor threads die → trades stuck in "pending"
```

### After (CSV + SQLite)
```
1. Signal → save to CSV + create SQLite record
2. Price monitor → update CSV + close SQLite record
3. Gate reads CSV (for now) → counts accurately
4. SQLite query → accurate trade state at any time
5. If monitor thread dies → stale trades can be recovered via fix_stale_trades.py
6. Future: Dashboard reads SQLite directly (CSV deprecated)
```

---

## Monitoring Recommendations

### Daily
```bash
# Check for stale trades
python diagnose_engine.py | grep -A5 "FILE TIMESTAMPS"

# If any coin not updated in >60 min:
python fix_stale_trades.py
python fix_stale_trades.py --apply
```

### Per Cycle (if needed)
```bash
# Verify trades are being recorded
python dashboard_sqlite.py | head -30
```

### On Restart
```bash
# Verify engine state is consistent
python diagnose_engine.py

# Check for stale trades from previous session
python fix_stale_trades.py
```

---

## Key Improvements

### Trade Tracking
- **Before**: Append-only CSV with duplicate rows for state changes
- **After**: Single row per trade in SQLite, proper state transitions

### Error Visibility
- **Before**: Exception stack traces swallowed in background processes
- **After**: Full traceback printed to console (visible in systemd journal)

### Diagnostics
- **Before**: Manual inspection of CSV files, hard to debug
- **After**: One-command health check, real-time dashboard

### Reliability
- **Before**: No way to recover from stale trades except manual CSV edit
- **After**: Automated recovery via `fix_stale_trades.py --apply`

---

## Database

SQLite database location: `./trades.db`

### Inspect
```bash
sqlite3 trades.db
# View pending trades
> SELECT timestamp, coin, signal, entry_price FROM trades WHERE state='PENDING';

# View recent closes
> SELECT timestamp, coin, outcome, close_price FROM trades WHERE state='CLOSED' ORDER BY timestamp DESC LIMIT 10;

# Stats by coin
> SELECT coin, state, COUNT(*) FROM trades GROUP BY coin, state;
```

### Export
```python
from trade_store import get_trade_store
store = get_trade_store()

# Export all trades to CSV
store.export_to_csv("export.csv")

# Export SOL trades only
store.export_to_csv("sol_export.csv", coin="SOL")
```

---

## Testing

All components have been tested and verified:

```bash
# Verify imports
python -c "from main import *; print('✓ All imports OK')"

# Test signal cycle
python -c "from main import run_cycle, COINS; run_cycle(COINS[1])"  # SOL

# Check database
sqlite3 trades.db "SELECT COUNT(*) FROM trades;"

# View dashboard
python dashboard_sqlite.py
```

---

## Next Steps (Optional)

### Short-term
- [ ] Add automated daily stale trade cleanup
- [ ] Monitor thread health check and recovery
- [ ] Centralized logging to file (not just stdout)

### Medium-term
- [ ] Migrate `dashboard_metrics.py` to read from SQLite
- [ ] Remove CSV dependency once proven stable (1-2 weeks)
- [ ] Add trade outcome confirmation (verify TP/SL prices)

### Long-term
- [ ] Implement trade recovery on restart (resume monitoring)
- [ ] Add trade amendment (adjust TP/SL mid-trade)
- [ ] Historical backfill from CSV to SQLite

---

## Troubleshooting

### Coins not generating signals
```bash
python diagnose_engine.py
```
Look for:
- ✗ API down → wait for Kraken recovery
- ⚠ File timestamps old → run `fix_stale_trades.py --apply`
- ✓ Cycle counter growing → engine is running

### Stale trades blocking gate
```bash
python fix_stale_trades.py
python fix_stale_trades.py --apply
```
This marks trades > 2 hours as losses and clears the gate.

### SQLite database corrupted
```bash
rm trades.db
python -c "from trade_store import get_trade_store; get_trade_store()"
```
Database will be recreated on next import.

---

## Files Reference

### Core Trading
- `main.py` — Engine orchestration (3-minute cycle scheduler)
- `step1_fetch.py` through `step12_output.py` — Trading pipeline
- `config.py` — Strategy parameters

### New Trade Storage
- `trade_store.py` — SQLite backend
- `trade_store_integration.py` — Pipeline integration
- `trades.db` — SQLite database file

### Diagnostics & Recovery
- `fix_stale_trades.py` — Recover blocked gates
- `diagnose_engine.py` — Health check
- `dashboard_sqlite.py` — Trading dashboard
- `INTEGRATION_SUMMARY.md` — Technical deep-dive

---

## Questions?

For technical details, see `INTEGRATION_SUMMARY.md` in this directory.
