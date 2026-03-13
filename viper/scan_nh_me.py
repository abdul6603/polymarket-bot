"""Targeted Viper scans for NH + ME — Jordan's 7 AM deadline.

NH: real estate + commercial real estate
ME: dental practice + real estate + commercial real estate
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from viper.mass_scan import scan_city_niche, _send_tg, DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scan_nh_me")

ET = timezone(timedelta(hours=-5))

# ── Scan Targets ─────────────────────────────────────────────────────
NH_CITIES = [
    "Portsmouth NH",
    "Manchester NH",
    "Nashua NH",
    "Concord NH",
    "Keene NH",
    "Laconia NH",
]

ME_CITIES = [
    "Portland ME",
    "Lewiston ME",
    "Bangor ME",
    "Augusta ME",
    "Waterville ME",
    "Rockland ME",
]

SCANS = [
    # (niche_search_term, cities)
    ("real estate", NH_CITIES),
    ("commercial real estate", NH_CITIES),
    ("dental practice", ME_CITIES),
    ("real estate", ME_CITIES),
    ("commercial real estate", ME_CITIES),
]


def main() -> int:
    start = datetime.now(ET)
    log.info("=== NH + ME SCAN START — %s ===", start.strftime("%H:%M ET"))

    total_scans = sum(len(cities) for _, cities in SCANS)
    log.info("Total scan combos: %d", total_scans)

    all_results: list[dict] = []
    done = 0

    for niche, cities in SCANS:
        state = cities[0].split()[-1]  # NH or ME
        log.info("── %s %s (%d cities) ──", niche.upper(), state, len(cities))

        for city in cities:
            try:
                result = scan_city_niche(niche, city)
                all_results.append(result)
                log.info(
                    "  %s: found=%d, qualified=%d, queued=%d",
                    city, result["found"], result["qualified"], result["queued"],
                )
            except Exception as e:
                log.error("  %s FAILED: %s", city, e)
                all_results.append({
                    "niche": niche, "city": city, "found": 0,
                    "qualified": 0, "queued": 0, "error": str(e),
                })

            done += 1
            elapsed = (datetime.now(ET) - start).total_seconds()
            rate = elapsed / done if done else 0
            remaining = (total_scans - done) * rate
            eta = datetime.now(ET) + timedelta(seconds=remaining)
            log.info("  Progress: %d/%d — ETA: %s ET", done, total_scans, eta.strftime("%H:%M"))

            time.sleep(8)  # avoid rate limits

    # ── Save results ─────────────────────────────────────────────────
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_file = DATA_DIR / f"scan_nh_me_{start.strftime('%Y_%m_%d')}.json"
    out_file.write_text(json.dumps({
        "scan_date": start.isoformat(),
        "results": all_results,
    }, indent=2, ensure_ascii=False))

    # ── Summary ──────────────────────────────────────────────────────
    total_found = sum(r.get("found", 0) for r in all_results)
    total_queued = sum(r.get("queued", 0) for r in all_results)
    errors = [r for r in all_results if r.get("error")]

    elapsed_min = (datetime.now(ET) - start).total_seconds() / 60

    # Group by state+niche
    groups: dict[str, dict] = {}
    for r in all_results:
        state = r["city"].split()[-1] if r.get("city") else "?"
        key = f"{state} {r['niche']}"
        if key not in groups:
            groups[key] = {"found": 0, "queued": 0}
        groups[key]["found"] += r.get("found", 0)
        groups[key]["queued"] += r.get("queued", 0)

    lines = [f"<b>NH + ME SCAN COMPLETE</b> ({elapsed_min:.0f} min)\n"]
    for key, stats in groups.items():
        lines.append(f"  {key}: {stats['found']} found, {stats['queued']} queued")
    lines.append(f"\nTotal: {total_found} found, {total_queued} queued")
    if errors:
        lines.append(f"\n{len(errors)} errors — check logs")

    summary = "\n".join(lines)
    _send_tg(summary)
    log.info(summary.replace("<b>", "").replace("</b>", ""))

    return 0


if __name__ == "__main__":
    sys.exit(main())
