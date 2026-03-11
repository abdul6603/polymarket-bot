"""Demo Pipeline Orchestrator — URL to live demo + video previews."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from viper.demos.scraper import scrape_business, ScrapedBusiness
from viper.demos.qa_generator import generate_qa_pairs, QAPair
from viper.demos.html_builder import build_demo_html
from viper.demos.deploy import deploy_demo

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

DATA_DIR = Path.home() / "polymarket-bot" / "data" / "demos"


def run_demo_pipeline(url: str, niche: str = "auto") -> dict:
    """Full pipeline: URL -> live demo + video previews.

    Returns dict with: slug, url, demo_url, quality, status
    """
    slug = _url_to_slug(url)
    output_dir = DATA_DIR / slug
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "slug": slug,
        "source_url": url,
        "niche": niche,
        "demo_url": None,
        "quality": 0,
        "status": "running",
        "timestamp": datetime.now(ET).isoformat(),
    }

    # Step 1: Scrape
    log.info("[1/5] Scraping %s...", url)
    biz = scrape_business(url, niche=niche)
    result["niche"] = biz.niche
    result["quality"] = biz.quality_score
    result["business_name"] = biz.name

    scraped_path = output_dir / "scraped.json"
    scraped_path.write_text(json.dumps(biz.to_dict(), indent=2, ensure_ascii=False))
    log.info("[1/5] Scraped: %s (quality=%d)", biz.name, biz.quality_score)

    # Step 2: Generate Q&A pairs
    log.info("[2/5] Generating Q&A pairs...")
    qa_pairs = generate_qa_pairs(biz)

    qa_path = output_dir / "qa_pairs.json"
    qa_path.write_text(json.dumps(
        [p.to_dict() for p in qa_pairs], indent=2, ensure_ascii=False,
    ))
    log.info("[2/5] Generated %d Q&A pairs", len(qa_pairs))

    # Step 3: Build HTML demo
    log.info("[3/5] Building HTML demo...")
    html_content = build_demo_html(biz, qa_pairs)

    demo_path = output_dir / "demo.html"
    demo_path.write_text(html_content, encoding="utf-8")
    log.info("[3/5] Demo HTML built (%d bytes)", len(html_content))

    # Step 4: Deploy to GitHub Pages
    log.info("[4/5] Deploying to GitHub Pages...")
    demo_url = deploy_demo(slug, html_content)
    result["demo_url"] = demo_url
    if demo_url:
        log.info("[4/5] Deployed: %s", demo_url)
    else:
        log.warning("[4/5] Deploy failed — demo available locally at %s", demo_path)
        result["demo_url"] = f"file://{demo_path}"

    # Step 5: Notify
    log.info("[5/5] Sending notification...")
    _notify(biz, demo_url)

    result["status"] = "complete"
    meta_path = output_dir / "meta.json"
    meta_path.write_text(json.dumps(result, indent=2))

    log.info("Pipeline complete: %s -> %s (quality=%d)",
             biz.name, demo_url, biz.quality_score)
    return result


def _url_to_slug(url: str) -> str:
    """Convert URL to a filesystem-safe slug."""
    slug = re.sub(r'^https?://(www\.)?', '', url)
    slug = slug.split('/')[0]
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', slug)
    slug = slug.strip('-').lower()
    return slug[:50]


def _notify(biz: ScrapedBusiness, demo_url: str | None) -> None:
    """Push notification to Shelby."""
    try:
        from shared.events import publish as bus_publish
        summary = (
            f"Demo built for {biz.name}!\n"
            f"URL: {demo_url or 'deploy failed'}\n"
            f"Quality: {biz.quality_score}/100\n"
            f"Niche: {biz.niche}\n"
            f"Video previews included (horizontal + vertical)"
        )
        bus_publish(
            agent="viper",
            event_type="demo_built",
            data={
                "business": biz.name,
                "url": demo_url,
                "quality": biz.quality_score,
                "niche": biz.niche,
            },
            summary=summary,
        )
    except Exception as e:
        log.warning("Shelby notification failed: %s", e)

    try:
        tasks_file = Path.home() / "shelby" / "data" / "tasks.json"
        tasks = []
        if tasks_file.exists():
            tasks = json.loads(tasks_file.read_text())

        tasks.append({
            "title": f"[VIPER] Demo ready for {biz.name}",
            "description": (
                f"Demo URL: {demo_url or 'check local file'}\n"
                f"Quality: {biz.quality_score}/100\n"
                f"Niche: {biz.niche}\n\n"
                f"Demo includes video previews (horizontal + vertical). "
                f"Review and approve for outreach."
            ),
            "priority": "high",
            "status": "pending",
            "from": "viper",
            "created_at": datetime.now(ET).isoformat(),
            "done": False,
        })

        tasks_file.parent.mkdir(parents=True, exist_ok=True)
        tasks_file.write_text(json.dumps(tasks, indent=2))
    except Exception as e:
        log.warning("Failed to write Shelby task: %s", e)
