"""GitHub Pages Deployer — push demos to shareable URLs."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

GH_CLI = Path.home() / "bin" / "gh"
REPO_NAME = "chatbot-demos"
REPO_OWNER = "abdul6603"
REPO_DIR = Path.home() / "polymarket-bot" / "data" / "demos" / "_repo"
BASE_URL = f"https://{REPO_OWNER}.github.io/{REPO_NAME}"


def deploy_demo(slug: str, html_content: str) -> str | None:
    """Deploy a demo HTML page to GitHub Pages.

    Returns the live URL or None on failure.
    """
    try:
        _ensure_repo()
        _write_demo(slug, html_content)
        _push(slug)
        url = f"{BASE_URL}/{slug}/"
        log.info("Deployed demo: %s", url)
        return url
    except Exception as e:
        log.exception("Deploy failed for %s: %s", slug, e)
        return None


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a shell command."""
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=60,
    )


def _ensure_repo() -> None:
    """Create the GitHub repo and clone it if needed."""
    if REPO_DIR.exists() and (REPO_DIR / ".git").exists():
        # Pull latest
        _run(["git", "pull", "--rebase"], cwd=REPO_DIR)
        return

    # Check if repo exists on GitHub
    result = _run([str(GH_CLI), "repo", "view", f"{REPO_OWNER}/{REPO_NAME}"])
    if result.returncode != 0:
        # Create the repo
        log.info("Creating GitHub repo: %s/%s", REPO_OWNER, REPO_NAME)
        create_result = _run([
            str(GH_CLI), "repo", "create", REPO_NAME,
            "--public", "--description", "AI Chatbot Demo Pages",
        ])
        if create_result.returncode != 0:
            raise RuntimeError(f"Failed to create repo: {create_result.stderr}")

    # Clone
    REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    if REPO_DIR.exists():
        shutil.rmtree(REPO_DIR)

    clone_result = _run([
        str(GH_CLI), "repo", "clone", f"{REPO_OWNER}/{REPO_NAME}", str(REPO_DIR),
    ])
    if clone_result.returncode != 0:
        raise RuntimeError(f"Failed to clone repo: {clone_result.stderr}")

    # Ensure there's at least one commit (GitHub Pages needs this)
    readme = REPO_DIR / "README.md"
    if not readme.exists():
        readme.write_text("# Chatbot Demos\n\nAI-powered chatbot demos by DarkCode AI.\n")
        _run(["git", "add", "."], cwd=REPO_DIR)
        _run(["git", "commit", "-m", "Initial commit"], cwd=REPO_DIR)
        _run(["git", "push", "-u", "origin", "main"], cwd=REPO_DIR)


def _write_demo(slug: str, html_content: str) -> None:
    """Write demo HTML to the repo directory."""
    demo_dir = REPO_DIR / slug
    demo_dir.mkdir(parents=True, exist_ok=True)
    index = demo_dir / "index.html"
    index.write_text(html_content, encoding="utf-8")
    log.info("Wrote demo to %s", index)


def _push(slug: str) -> None:
    """Commit and push the demo."""
    _run(["git", "add", "."], cwd=REPO_DIR)

    result = _run(["git", "status", "--porcelain"], cwd=REPO_DIR)
    if not result.stdout.strip():
        log.info("No changes to push for %s", slug)
        return

    _run(
        ["git", "commit", "-m", f"Add/update demo: {slug}"],
        cwd=REPO_DIR,
    )
    push_result = _run(["git", "push"], cwd=REPO_DIR)
    if push_result.returncode != 0:
        # Try setting upstream
        _run(["git", "push", "-u", "origin", "main"], cwd=REPO_DIR)
