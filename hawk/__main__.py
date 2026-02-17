"""Entry point: .venv/bin/python -m hawk"""
import asyncio
from hawk.main import HawkBot

if __name__ == "__main__":
    bot = HawkBot()
    asyncio.run(bot.run())
