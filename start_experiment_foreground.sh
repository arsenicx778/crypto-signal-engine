#!/bin/zsh
# Starts the A/B/C experiment + dashboard in the FOREGROUND.
# Ctrl+C stops both. Engine output streams live to the terminal.
# Dashboard runs in the background — its request logs go to /tmp/dashboard.log
# so they don't pollute the engine output.

cd "$(dirname "$0")" || exit 1

echo "=== Stopping any existing processes ==="
pkill -TERM -f "experiment.runner" 2>/dev/null
pkill -TERM -f "dashboard.py"      2>/dev/null
pkill -TERM -f "main.py"           2>/dev/null
sleep 3
pkill -KILL -f "experiment.runner" 2>/dev/null
pkill -KILL -f "dashboard.py"      2>/dev/null
pkill -KILL -f "main.py"           2>/dev/null
lsof -ti:8765 | xargs kill -9 2>/dev/null
sleep 1
LEFTOVER=$(pgrep -f "experiment.runner|dashboard.py|main.py" | wc -l | tr -d ' ')
if [ "$LEFTOVER" -gt "0" ]; then
  echo "  WARNING: $LEFTOVER stale process(es) still running:"
  pgrep -fl "experiment.runner|dashboard.py|main.py"
  echo "  Aborting to avoid duplicate runners. Manually kill them, then re-run."
  exit 1
fi
echo "  clean — no stale processes"

echo "=== Starting dashboard (port 8765, logs to /tmp/dashboard.log) ==="
caffeinate -dimsu ./.venv/bin/python -u dashboard.py \
  > /tmp/dashboard.log 2>&1 &
DASH_PID=$!
sleep 2

if ! ps -p $DASH_PID > /dev/null; then
  echo "  dashboard FAILED — check /tmp/dashboard.log"
  exit 1
fi
echo "  dashboard:  PID $DASH_PID  ->  http://localhost:8765"

# Trap Ctrl+C so we kill the background dashboard when the user stops the runner.
cleanup() {
  echo ""
  echo "=== Stopping engine + dashboard ==="
  kill $DASH_PID 2>/dev/null
  pkill -f "experiment.runner" 2>/dev/null
  exit 0
}
trap cleanup INT TERM

echo "=== Starting experiment runner (foreground — Ctrl+C to stop both) ==="
echo "=== Variants A, B, C on ETH | Cycle: 180s ==="
echo ""

# Foreground — output streams to this terminal
caffeinate -dimsu ./.venv/bin/python -u -m experiment.runner

# If runner exits on its own, clean up dashboard too
cleanup
