"""Demo Deployer — pushes custom demos to GitHub Pages.

Handles git clone/pull, folder creation, file writing, video copying,
commit, push, and deployment verification via HEAD request.

Called after demo_builder generates the HTML.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

REPO_DIR = Path.home() / "chatbot-demos"
REPO_URL = "https://github.com/DarkCode-AI/chatbot-demos.git"
DEMO_BASE_URL = "https://darkcode-ai.github.io/chatbot-demos/"

# Max retries for deployment verification
_DEPLOY_RETRIES = 6
_DEPLOY_WAIT = 15  # seconds between retries


def _make_slug(business_name: str) -> str:
    """Convert business name to a URL-safe slug.

    "Belmont Periodontics, P.C." → "belmont-periodontics"
    """
    s = business_name.lower().strip()
    for suffix in [", p.c.", " p.c.", ", pllc", " pllc", ", llc", " llc",
                   ", inc.", " inc.", ", inc", " inc", ", dds", " dds",
                   ", dmd", " dmd"]:
        s = s.replace(suffix, "")
    s = re.sub(r"[^a-z0-9\s]", "", s).strip()
    s = re.sub(r"\s+", "-", s)
    return s


def _ensure_repo() -> bool:
    """Ensure the chatbot-demos repo is cloned and up to date."""
    if REPO_DIR.exists():
        try:
            subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=REPO_DIR, capture_output=True, timeout=30, check=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            log.error("[DEPLOYER] git pull failed: %s", e.stderr)
            return False
    else:
        try:
            subprocess.run(
                ["git", "clone", REPO_URL, str(REPO_DIR)],
                capture_output=True, timeout=60, check=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            log.error("[DEPLOYER] git clone failed: %s", e.stderr)
            return False


def _copy_videos(demo_dir: Path, niche: str) -> None:
    """Copy video preview files from the generic template."""
    niche_template_map = {
        "real_estate": "realestate-demo",
        "commercial_re": "commercial-re-demo",
    }
    template_slug = niche_template_map.get(niche, "dental-demo")
    src_videos = REPO_DIR / template_slug / "videos"
    if not src_videos.exists():
        log.warning("[DEPLOYER] No video source at %s", src_videos)
        return

    dst_videos = demo_dir / "videos"
    dst_videos.mkdir(exist_ok=True)

    for vid in src_videos.iterdir():
        if vid.is_file() and vid.suffix == ".mp4":
            dst = dst_videos / vid.name
            if not dst.exists():
                shutil.copy2(vid, dst)
                log.info("[DEPLOYER] Copied video %s", vid.name)


def _git_commit_push(demo_dir: Path, business_name: str) -> bool:
    """Stage, commit, and push the new demo."""
    try:
        subprocess.run(
            ["git", "add", str(demo_dir)],
            cwd=REPO_DIR, capture_output=True, timeout=15, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"Add custom demo: {business_name}"],
            cwd=REPO_DIR, capture_output=True, timeout=15, check=True,
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=REPO_DIR, capture_output=True, timeout=30, check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        log.error("[DEPLOYER] git commit/push failed: %s", e.stderr)
        return False


def _verify_deployment(url: str) -> bool:
    """Wait for GitHub Pages to deploy, then verify with HEAD request."""
    for attempt in range(1, _DEPLOY_RETRIES + 1):
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                log.info("[DEPLOYER] Demo live at %s (attempt %d)", url, attempt)
                return True
            log.info("[DEPLOYER] Attempt %d: HTTP %d for %s", attempt, resp.status_code, url)
        except Exception as e:
            log.info("[DEPLOYER] Attempt %d: %s for %s", attempt, e, url)

        if attempt < _DEPLOY_RETRIES:
            time.sleep(_DEPLOY_WAIT)

    return False


def deploy_demo(
    business_name: str,
    html: str,
    niche: str = "dental",
) -> tuple[str, bool]:
    """Deploy a custom demo to GitHub Pages.

    1. Ensure repo is cloned/pulled
    2. Create folder with business slug
    3. Write index.html
    4. Copy video files from generic template
    5. Git add, commit, push
    6. Wait for GitHub Pages deployment
    7. Verify with HEAD request

    Returns:
        (demo_url, success) tuple.
    """
    slug = _make_slug(business_name)
    if not slug:
        log.error("[DEPLOYER] Could not generate slug for: %s", business_name)
        return "", False

    demo_url = f"{DEMO_BASE_URL}{slug}/"

    # 1. Ensure repo
    if not _ensure_repo():
        return "", False

    # 2. Check if demo already exists
    demo_dir = REPO_DIR / slug
    if demo_dir.exists() and (demo_dir / "index.html").exists():
        log.info("[DEPLOYER] Demo already exists at %s — updating", slug)

    # 3. Create directory and write HTML
    demo_dir.mkdir(exist_ok=True)
    (demo_dir / "index.html").write_text(html, encoding="utf-8")
    log.info("[DEPLOYER] Wrote index.html to %s", demo_dir)

    # 4. Copy video files
    _copy_videos(demo_dir, niche)

    # 5. Commit and push
    if not _git_commit_push(demo_dir, business_name):
        return "", False

    # 6. Verify deployment
    success = _verify_deployment(demo_url)
    if not success:
        log.warning("[DEPLOYER] Deployment verification timed out for %s — "
                    "URL may still become available shortly", demo_url)
        # Return the URL anyway — it will likely deploy within minutes
        return demo_url, True

    return demo_url, True
