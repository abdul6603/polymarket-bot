"""Entry point: .venv/bin/python -m razor"""
import asyncio
from razor.main import RazorBot

if __name__ == "__main__":
    bot = RazorBot()
    asyncio.run(bot.run())
