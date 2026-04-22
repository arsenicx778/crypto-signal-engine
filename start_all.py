#!/usr/bin/env python3

import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main():
    processes = []

    def shutdown(*_args):
        print("\nStopping engine and dashboard...")
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("Starting engine...")
    engine = subprocess.Popen([sys.executable, "main.py"], cwd=ROOT)
    processes.append(engine)

    time.sleep(3)

    print("Starting dashboard...")
    dashboard = subprocess.Popen([sys.executable, "dashboard.py"], cwd=ROOT)
    processes.append(dashboard)

    print("Engine and dashboard are starting together.")
    print("Press Ctrl+C to stop both.")

    while True:
        if engine.poll() is not None:
            print("Engine exited.")
            shutdown()
        if dashboard.poll() is not None:
            print("Dashboard exited.")
            shutdown()
        time.sleep(1)


if __name__ == "__main__":
    main()
