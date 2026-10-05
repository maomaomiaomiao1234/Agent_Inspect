#!/usr/bin/env python3
"""Rebuild the README live panel from docs/live-panel/config.json.

Requires Python 3.11+, Chrome/Chromium and ffmpeg. No Python packages needed.
The vendored live-panel engine and its MIT license live in scripts/live_panel/.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "scripts" / "live_panel"
TEMPLATE = ENGINE / "assets" / "template.html"
CONFIG = ROOT / "docs" / "live-panel" / "config.json"
IMAGES = ROOT / "docs" / "images"
sys.path.insert(0, str(ENGINE))
import livepanel as lp  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", help="Chrome/Chromium executable")
    parser.add_argument("--ffmpeg", help="ffmpeg executable")
    args = parser.parse_args()
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    chrome = lp.find_exe(
        args.chrome or (str(mac_chrome) if mac_chrome.exists() else None), lp.CHROME_NAMES, "Chrome"
    )
    ffmpeg = lp.find_exe(args.ffmpeg, ["ffmpeg"], "ffmpeg")
    common = ["--config", str(CONFIG), "--template", str(TEMPLATE), "--chrome", chrome]
    IMAGES.mkdir(exist_ok=True)
    page = ROOT / "docs" / "live-panel" / "index.html"
    video = IMAGES / "agent-inspect-live-panel.mp4"
    gif = IMAGES / "agent-inspect-live-panel.gif"
    poster = IMAGES / "agent-inspect-live-panel.png"

    # Validate all states before producing the README assets; the checker also
    # verifies that seeking away and back produces identical sampled frames.
    with tempfile.TemporaryDirectory(prefix="agent-inspect-panel-check-") as frames:
        subprocess.run(
            [sys.executable, str(ENGINE / "check_frames.py"), *common, "--out-dir", frames, "--repeat"],
            check=True,
        )
    subprocess.run(
        [
            sys.executable,
            str(ENGINE / "render.py"),
            *common,
            "--ffmpeg",
            ffmpeg,
            "--out",
            str(video),
            "--html-out",
            str(page),
            "--audio",
            "none",
        ],
        check=True,
    )
    # Keep the upstream license with the self-contained HTML when downloaded.
    license_text = (ENGINE / "LICENSE").read_text(encoding="utf-8")
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "<!doctype html>", "<!doctype html>\n<!--\n" + license_text + "\n-->"
        ),
        encoding="utf-8",
    )
    # A single palette and differential frames keep README playback small and
    # preserve crisp text. GIF works directly in GitHub's Markdown image tags.
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-filter_complex",
            "fps=12,scale=1200:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
            "-loop",
            "0",
            str(gif),
        ],
        check=True,
    )
    config = lp.load_config(CONFIG)
    width, height, _, _ = lp.canvas(config)
    with lp.Chrome(chrome, width, height) as browser:
        browser.open(page.as_uri() + "?manual")
        browser.seek(1.5)
        poster.write_bytes(browser.shot())
    for output in (gif, poster, video, page):
        print(f"Created {output.relative_to(ROOT)} ({output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
