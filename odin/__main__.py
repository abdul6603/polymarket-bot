"""Entry point: python -m odin"""
from __future__ import annotations

import asyncio
import sys

from odin.main import OdinBot


def main() -> None:
    bot = OdinBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\nOdin shutting down.")
        sys.exit(0)


if __name__ == "__main__":
    main()
