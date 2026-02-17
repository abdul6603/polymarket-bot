"""Entry point: .venv/bin/python -m viper"""
import asyncio
from viper.main import ViperBot

if __name__ == "__main__":
    bot = ViperBot()
    asyncio.run(bot.run())
