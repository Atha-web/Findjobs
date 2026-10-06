"""
Runs the Telegram poller and the scheduler side by side in one container, and restarts
either one if it exits. This is what the Docker image starts; locally you use
start_agents.ps1 instead.

Run exactly one copy of this anywhere: two pollers on one bot token fight over Telegram
updates, and two schedulers would run every job twice.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

SERVICES = {
    "poller": ["poll_telegram.py"],
    "scheduler": ["scheduler.py"],
}
RESTART_DELAY_SECONDS = 10


def main() -> None:
    procs: dict[str, subprocess.Popen] = {}
    stopping = False

    def stop(*_args) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while not stopping:
        for name, args in SERVICES.items():
            proc = procs.get(name)
            if proc is None or proc.poll() is not None:
                if proc is not None:
                    print(f"[run_all] {name} exited with {proc.returncode}; restarting in "
                          f"{RESTART_DELAY_SECONDS}s", flush=True)
                    time.sleep(RESTART_DELAY_SECONDS)
                procs[name] = subprocess.Popen([sys.executable, "-u", *args])
                print(f"[run_all] started {name}", flush=True)
        time.sleep(2)

    for proc in procs.values():
        proc.terminate()
    for proc in procs.values():
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
