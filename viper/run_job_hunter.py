"""Viper Job Hunter — standalone runner.

Runs the job scanner loop AND the Telegram callback listener in parallel.
"""
from __future__ import annotations

import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/polymarket-bot/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from viper.job_hunter import run_scan, run_loop
from viper.tg_listener import poll_callbacks


def main():
    interval = int(os.getenv("VIPER_JOB_SCAN_INTERVAL", "30"))

    if "--loop" in sys.argv:
        # Start TG callback listener in background thread
        listener_thread = threading.Thread(target=poll_callbacks, daemon=True)
        listener_thread.start()
        logging.getLogger(__name__).info("TG callback listener started in background")

        # Run scanner in main thread
        run_loop(interval_minutes=interval)
    else:
        result = run_scan()
        print(f"Scan complete: {result}")


if __name__ == "__main__":
    main()
