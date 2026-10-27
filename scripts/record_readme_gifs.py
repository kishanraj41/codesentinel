"""Record the README demo GIFs from the local web demo (no API keys).

Start the demo first, from web/:

    uvicorn app.main:app --host 127.0.0.1 --port 8765

Then:

    python scripts/record_readme_gifs.py

Uses Playwright Chromium and ffmpeg. Frames are synthetic charts only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets"
RAW = OUT / "_raw"
BASE = os.environ.get("CODESENTINEL_DEMO", "http://127.0.0.1:8765")


def _chrome() -> str:
    override = os.environ.get("CODESENTINEL_CHROME", "").strip()
    if override:
        return override
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    candidates = sorted(root.glob("chromium-*/chrome-win64/chrome.exe"))
    if not candidates:
        raise SystemExit("Playwright Chromium not found. Run: python -m playwright install chromium")
    return str(candidates[-1])


CHROME = _chrome()
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")


def _launch(playwright):
    return playwright.chromium.launch(
        headless=True,
        executable_path=CHROME,
        args=["--headless=new", "--disable-gpu", "--font-render-hinting=none"],
    )


def _prepare(page) -> None:
    page.add_init_script("localStorage.setItem('codesentinel-coach-v1','done');")
    page.goto(BASE, wait_until="networkidle")
    page.evaluate(
        """() => {
            localStorage.setItem('codesentinel-coach-v1','done');
            const coach = document.getElementById('coach');
            if (coach) coach.hidden = true;
        }"""
    )


def _record(name: str, action) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(RAW),
            record_video_size={"width": 1280, "height": 800},
            device_scale_factor=1,
            color_scheme="light",
        )
        page = context.new_page()
        action(page)
        page.close()
        video_path = Path(page.video.path())
        context.close()
        browser.close()
    dest = RAW / f"{name}.webm"
    if video_path.resolve() != dest.resolve():
        shutil.move(str(video_path), dest)
    return dest


def findings(page) -> None:
    _prepare(page)
    page.wait_for_timeout(900)
    page.get_by_role("button", name="Audit chart, Margaret Whitmore").click()
    page.wait_for_selector("#work-heading")
    page.wait_for_timeout(2200)
    page.mouse.wheel(0, 320)
    page.wait_for_timeout(1800)


def evidence(page) -> None:
    _prepare(page)
    page.wait_for_timeout(500)
    page.get_by_role("button", name="Audit chart, Robert Williams").click()
    page.wait_for_selector("#work-heading")
    page.wait_for_timeout(700)
    row = page.locator("#worklist article").filter(has_text="E11.22").first
    row.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    row.get_by_role("button", name="Evidence", exact=True).click()
    page.wait_for_timeout(400)
    row.locator("text=Secondary source").scroll_into_view_if_needed()
    page.wait_for_timeout(3200)


def review(page) -> None:
    _prepare(page)
    page.wait_for_timeout(400)
    page.get_by_role("button", name="Audit chart, Margaret Whitmore").click()
    page.wait_for_selector("#work-heading")
    page.wait_for_timeout(800)
    rows = page.locator("#worklist article")
    rows.nth(0).get_by_role("button", name="Accept", exact=True).click()
    page.wait_for_timeout(900)
    rows.nth(1).get_by_role("button", name="Dismiss", exact=True).click()
    page.wait_for_timeout(1100)
    page.get_by_role("tab", name="Eval").click()
    page.wait_for_selector("table.bench")
    page.wait_for_timeout(2200)


def _gif(webm: Path, gif: Path) -> None:
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-i",
            str(webm),
            "-vf",
            "fps=10,scale=1200:-1:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=diff:max_colors=80[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3",
            "-loop",
            "0",
            str(gif),
        ],
        check=True,
    )


def main() -> None:
    jobs = (
        ("audit-findings", findings),
        ("guideline-evidence", evidence),
        ("review-and-eval", review),
    )
    for name, action in jobs:
        webm = _record(name, action)
        gif = OUT / f"{name}.gif"
        _gif(webm, gif)
        webm.unlink(missing_ok=True)
        print(f"{gif.name} {gif.stat().st_size} bytes")


if __name__ == "__main__":
    main()
