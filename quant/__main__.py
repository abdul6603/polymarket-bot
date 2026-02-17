"""Entry point: python -m quant"""
from __future__ import annotations

import asyncio
from quant.main import QuantBot

if __name__ == "__main__":
    asyncio.run(QuantBot().run())
