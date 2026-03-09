"""CLI entry point for the demo pipeline."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path.home() / "polymarket-bot"))
sys.path.insert(0, str(Path.home() / "shared"))

from viper.demos.pipeline import run_demo_pipeline


def main():
    parser = argparse.ArgumentParser(
        description="Build a personalized chatbot demo from a business URL",
    )
    parser.add_argument("url", help="Business website URL")
    parser.add_argument(
        "--niche", default="auto",
        choices=["auto", "dental", "real_estate"],
        help="Business niche (default: auto-detect)",
    )
    parser.add_argument(
        "--no-deploy", action="store_true",
        help="Skip GitHub Pages deployment (local-only)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    result = run_demo_pipeline(args.url, niche=args.niche)

    print("\n" + "=" * 60)
    print(f"  Business:  {result.get('business_name', 'Unknown')}")
    print(f"  Niche:     {result.get('niche', 'auto')}")
    print(f"  Quality:   {result.get('quality', 0)}/100")
    print(f"  Demo URL:  {result.get('demo_url', 'N/A')}")
    print(f"  Status:    {result.get('status', 'unknown')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
