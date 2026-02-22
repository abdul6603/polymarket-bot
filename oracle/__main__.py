"""Entry point: python -m oracle"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from oracle.main import OracleBot


def main() -> None:
    bot = OracleBot()
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
