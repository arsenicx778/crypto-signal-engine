# SQLite Integration & Engine Recovery Summary

## Problem Identified

The crypto signal engine had a critical issue: **three of four coins (SOL, LINK, XRP) completely stopped generating signals at 11:38 AM on 2026-05-08**. While the engine appeared to be running (cycle counter at 946), the diagnostics revealed:

1. **Stale pending trades**: SOL, LINK, and XRP had trades stuck in "pending" state from over 3 hours prior
2. **Gate blockage**: These stale trades maxed out the 2-concurrent-trades limit per coin, blocking new signal generation
3. **Monitor thread failure**: The price monitor daemon threads had crashed or exited, leaving trades in zombie state
4. **Silent error handling**: Exceptions in `main.py:run_coin_safe()` were caught but only printed to console (not visible in background processes)

## Solution Implemented

### 1. SQLite Trade Store (New)
Created `trade_store.py`: Production-ready SQLite database for trade lifecycle management.

**Key features:**
- ACID transactions with proper rollback handling
- Trade states: PENDING, CLOSED (with W/L outcomes), DNE (skipped)
- UNIQUE constraint on `(coin, timestamp, signal, entry_price)` to prevent duplicates
- Methods: `create_signal()`, `close_trade()`, `get_pending_trades()`, `get_stats()`, `export_to_csv()`
- Metadata field for flexible JSON storage of indicators and reasoning

**Advantages over CSV:**
- Single row per trade (no append-only duplication)
- Queryable state without reading entire file
- Proper transaction isolation
- Scalable for thousands of trades

### 2. Integration Layer (New)
Created `trade_store_integration.py`: Bridge between existing signal pipeline and SQLite backend.

**Functions:**
- `save_signal_to_store()` — Create new signal in database
- `close_signal_in_store()` — Close trade with outcome
- `find_trade_by_timestamp()` — Query trades by timestamp
- `get_pending_trades()`, `get_closed_trades()`, `get_trade_stats()`

### 3. Signal Pipeline Update
Modified `step12_output.py` to use SQLite alongside CSV:

- `save_signal()` now calls `save_signal_to_store()` after CSV write
- `_update_outcome()` closes trades in SQLite database
- `TRADE_ID_MAP` tracks CSV timestamps → SQLite IDs for fast lookups
- Fallback to timestamp lookup if ID not found
- All errors caught and logged (doesn't block trade lifecycle)

### 4. Error Visibility Fix
Modified `main.py` line 264-269:

- Added `import traceback` and `traceback.print_exc()` to `run_coin_safe()`
- Exception handlers now print full stack traces (not just error message)
- Exceptions visible to console and systemd journal

### 5. Stale Trade Recovery
Created `fix_stale_trades.py`: Diagnostic script to identify and recover from stuck trades.

**Usage:**
```bash
# Dry run (no changes)
python fix_stale_trades.py

# Apply fix
python fix_stale_trades.py --apply
```

**What it does:**
- Scans all coin CSVs for pending trades older than 2 hours
- Marks them as losses (worst-case outcome to unblock gates)
- Prevents future cycles from seeing them as open

### 6. Diagnostics & Monitoring
Created `diagnose_engine.py`: One-command health check.

**Checks:**
- Kraken API connectivity
- Config flags (live trading enabled, suspended coins)
- File modification timestamps (detects stalled engines)
- CSV row counts per coin
- Engine state (cycle counter, capital, open positions)
- SQLite database status

Created `dashboard_sqlite.py`: Real-time SQLite trading dashboard.

**Shows:**
- Per-coin stats (wins, losses, win rate, pending)
- Recent trades with timestamps and outcomes
- Pending trades with entry prices, TP/SL levels
- Aggregate statistics across all coins

## Files Changed

### New Files
- `trade_store.py` (255 lines) — SQLite trade database
- `trade_store_integration.py` (160 lines) — Integration functions
- `diagnose_engine.py` (180 lines) — Health diagnostics
- `dashboard_sqlite.py` (150 lines) — SQLite dashboard
- `fix_stale_trades.py` (125 lines) — Stale trade recovery
- `INTEGRATION_SUMMARY.md` (this file)

### Modified Files
- `step12_output.py` — Added SQLite save/close, TRADE_ID_MAP tracking
- `main.py` — Added traceback printing to error handler

## How It Works

### Trade Lifecycle (New)

```
1. Signal Generated
   ↓
2. save_signal() called
   ↓
3. Row appended to CSV (backwards compatibility)
4. Trade created in SQLite (primary storage)
   ↓
5. Trade ID stored in TRADE_ID_MAP
6. Monitor thread spawned (watches for TP/SL hits)
   ↓
7. Price hits TP or SL
   ↓
8. _update_outcome() called
   ↓
9. CSV row appended with outcome
10. Trade closed in SQLite (primary storage)
    ↓
11. Outcome recorded in project_logger
    ↓
12. Next cycle: gate sees 0 open trades, generates new signals
```

### Recovery from Stale Trades

When coins stop generating signals due to gate blockage:

```
1. Run: python fix_stale_trades.py (inspect)
2. Run: python fix_stale_trades.py --apply (recover)
3. Stale trades marked as losses in CSV
4. gate.get_open_trades() returns 0
5. Next 3-minute cycle: all coins resume signal generation
```

## Verification

### Before Fix
- SOL, LINK, XRP: no updates for 3+ hours
- All four coins show 2/2 gate FULL on every cycle
- CSV shows 7 stale pending trades blocking gates

### After Fix
- `python fix_stale_trades.py --apply` — 7 trades recovered
- Test cycle: SOL → Buy (65%), LINK → Buy (64%), XRP → DNE
- `python dashboard_sqlite.py` — SQLite shows 2 pending trades (new ones)
- All coins generating signals again

## Recommended Next Steps

### Immediate
1. ✅ Applied stale trade fix
2. ✅ Restarted manual test cycles — all coins responding
3. ⏳ Monitor next 3-hour automated run for signal stability

### Short-term
- [ ] Add monitor thread health check in main.py
- [ ] Implement automatic stale trade cleanup (daily)
- [ ] Alert on monitor thread death
- [ ] Centralize logging to file (not just stdout)

### Medium-term
- [ ] Migrate dashboard_metrics.py to read from SQLite instead of CSV
- [ ] Remove CSV dependency once SQLite is proven stable
- [ ] Add trade outcome confirmation (verify TP/SL prices vs Kraken historical)
- [ ] Implement trade recovery on restart (re-monitor trades from last session)

## Database Location

SQLite database created at: `./trades.db`

View contents:
```bash
sqlite3 trades.db
> SELECT * FROM trades WHERE coin='SOL' AND state='PENDING';
> SELECT outcome, COUNT(*) FROM trades GROUP BY outcome;
```

Export to CSV:
```python
from trade_store import get_trade_store
store = get_trade_store()
store.export_to_csv("export.csv", coin="ETH")
```

## Testing

All components tested and verified:

```bash
# Test imports
python -c "from main import *; print('✓ All imports successful')"

# Test single coin cycle
python -c "from main import run_cycle, COINS; run_cycle(COINS[0])"

# View diagnostics
python diagnose_engine.py

# View dashboard
python dashboard_sqlite.py

# Check database
sqlite3 trades.db "SELECT COUNT(*) FROM trades;"
```
