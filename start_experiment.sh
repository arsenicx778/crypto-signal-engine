#!/bin/zsh
# Starts the A/B/C variant experiment + dashboard.
# Kills any existing processes first to avoid duplicate runners.

cd "$(dirname "$0")" || exit 1

echo "=== Stopping any existing processes ==="
# First polite SIGTERM
pkill -TERM -f "experiment.runner" 2>/dev/null
pkill -TERM -f "dashboard.py"      2>/dev/null
pkill -TERM -f "main.py"           2>/dev/null
sleep 3
# Anything still alive gets SIGKILL — prevents leftover runners racing the new one
pkill -KILL -f "experiment.runner" 2>/dev/null
pkill -KILL -f "dashboard.py"      2>/dev/null
pkill -KILL -f "main.py"           2>/dev/null
lsof -ti:8765 | xargs kill -9 2>/dev/null
sleep 1
# Verify nothing left
LEFTOVER=$(pgrep -f "experiment.runner|dashboard.py|main.py" | wc -l | tr -d ' ')
if [ "$LEFTOVER" -gt "0" ]; then
  echo "  WARNING: $LEFTOVER stale process(es) still running:"
  pgrep -fl "experiment.runner|dashboard.py|main.py"
  echo "  Aborting to avoid duplicate runners. Manually kill them, then re-run."
  exit 1
fi
echo "  clean — no stale processes"

echo "=== Starting dashboard (port 8765) ==="
nohup caffeinate -dimsu ./.venv/bin/python -u dashboard.py \
  > /tmp/dashboard.log 2>&1 &
DASH_PID=$!
sleep 2

echo "=== Starting experiment runner (variants A, B, C on ETH) ==="
nohup caffeinate -dimsu ./.venv/bin/python -u -m experiment.runner \
  > /tmp/experiment.log 2>&1 &
EXP_PID=$!
sleep 4

echo ""
echo "=== Status ==="
if ps -p $DASH_PID > /dev/null; then
  echo "  dashboard:        PID $DASH_PID  ->  http://localhost:8765"
else
  echo "  dashboard:        FAILED — check /tmp/dashboard.log"
fi

if ps -p $EXP_PID > /dev/null; then
  echo "  experiment:       PID $EXP_PID   ->  variants A, B, C"
else
  echo "  experiment:       FAILED — check /tmp/experiment.log"
fi

echo ""
echo "=== Live logs ==="
echo "  tail -f /tmp/experiment.log    # variant decisions"
echo "  tail -f /tmp/dashboard.log     # dashboard requests"
echo ""
echo "=== To stop everything ==="
echo "  pkill -f 'experiment.runner'; pkill -f 'dashboard.py'"
