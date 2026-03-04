#!/usr/bin/env python3
"""Best-effort NotebookLM web automation.

Design goals:
- No manual fallback inside script.
- Reuse persistent browser profile cookies in a dedicated profile dir.
- On failure, return non-zero with clear reason.

Output:
- Save 1..N images from NotebookLM presentation artifact only.
- Never fallback to generic page screenshots.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_PROFILE = Path.home() / "Library/Application Support/BaoyuNotebookLM"


def profile_in_use(profile_dir: Path) -> bool:
    try:
        out = subprocess.check_output(["ps", "aux"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    return "BaoyuNotebookLM" in out and "--user-data-dir=" in out


def _find_profile_pids() -> List[int]:
    try:
        out = subprocess.check_output(["ps", "aux"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    pids: List[int] = []
    for line in out.splitlines():
        if "BaoyuNotebookLM" not in line or "--user-data-dir=" not in line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def kill_profile_processes(profile_dir: Path) -> None:
    # profile_dir kept for signature compatibility
    _ = profile_dir
    pids = _find_profile_pids()
    if not pids:
        return
    for pid in pids:
        try:
            os.kill(pid, 15)
        except Exception:
            pass
    time.sleep(1.2)
    # hard-kill survivors
    for pid in _find_profile_pids():
        try:
            os.kill(pid, 9)
        except Exception:
            pass
    time.sleep(0.4)


def cleanup_stale_singleton(profile_dir: Path) -> None:
    # Chrome sometimes leaves stale singleton files after abnormal exit.
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = profile_dir / name
        try:
            if p.exists() or p.is_symlink():
                p.unlink()
        except Exception:
            pass


def _first_existing(page, selectors: Iterable[str]):
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if loc.count() > 0 and loc.first.is_visible(timeout=1200):
                return loc.first
        except Exception:
            continue
    return None


def click_any(page, selectors: List[str], timeout_ms: int = 20000) -> bool:
    t0 = time.time()
    while (time.time() - t0) * 1000 < timeout_ms:
        loc = _first_existing(page, selectors)
        if loc is not None:
            try:
                loc.click(timeout=1500)
                return True
            except Exception:
                pass
        page.wait_for_timeout(400)
    return False


def fill_any(page, selectors: List[str], value: str, timeout_ms: int = 20000) -> bool:
    t0 = time.time()
    while (time.time() - t0) * 1000 < timeout_ms:
        loc = _first_existing(page, selectors)
        if loc is not None:
            try:
                loc.fill(value, timeout=1500)
                return True
            except Exception:
                pass
        page.wait_for_timeout(400)
    return False


def wait_presentation_done(page, timeout_ms: int = 900000) -> None:
    # Wait up to 8 minutes. If still generating, fail hard.
    start = time.time()
    seen_generating = False
    while (time.time() - start) * 1000 < timeout_ms:
        texts = page.eval_on_selector_all(
            "button,[role='button']",
            "els => els.map(e => (e.innerText||'').trim()).filter(Boolean)",
        )
        joined = "\n".join(texts)
        # Retry generation once if explicit retry action appears.
        if any(k in joined for k in ["重试", "再试一次", "Try again", "Retry"]):
            click_any(
                page,
                [
                    "button:has-text('重试')",
                    "button:has-text('再试一次')",
                    "button:has-text('Try again')",
                    "button:has-text('Retry')",
                ],
                timeout_ms=2000,
            )
            page.wait_for_timeout(2500)
            seen_generating = True
            continue
        if "正在生成演示文稿" in joined:
            seen_generating = True
            page.wait_for_timeout(3000)
            continue
        # If we have seen generating and now it disappears, treat as complete.
        if seen_generating:
            return
        # If there is already a completed presentation tile from previous run, proceed.
        if "演示文稿" in joined and "正在生成演示文稿" not in joined:
            return
        page.wait_for_timeout(1200)
    raise RuntimeError("NotebookLM 演示文稿生成超时")


def capture_presentation_outputs(page, out_dir: Path, count: int) -> List[Path]:
    out: List[Path] = []
    # Strict mode: only capture presentation artifact widgets, never whole-page fallback.
    selectors = [
        "div.artifact-viewer",
        "div[class*='artifact'][class*='viewer']",
        "div[role='region'][aria-label*='演示文稿']",
        "div[role='region'][aria-label*='presentation' i]",
        "button[aria-label='演示文稿']",
        "button.artifact-button-content:has-text('演示文稿')",
        "div.artifact-button-content:has-text('演示文稿')",
        "div.create-artifact-button-container:has-text('演示文稿')",
    ]
    for sel in selectors:
        loc = page.locator(sel)
        n = min(loc.count(), max(0, count - len(out)))
        for i in range(n):
            it = loc.nth(i)
            try:
                if not it.is_visible(timeout=700):
                    continue
                p = out_dir / f"notebooklm-{len(out)+1}.png"
                it.screenshot(path=str(p))
                out.append(p)
                if len(out) >= count:
                    return out
            except Exception:
                continue
    return out


def _collect_images_from_download(file_path: Path, out_dir: Path) -> List[Path]:
    ext = file_path.suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp"} and file_path.exists():
        return [file_path]
    if ext == ".zip" and file_path.exists():
        unzip_dir = out_dir / f".notebooklm-unzip-{file_path.stem}"
        unzip_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(file_path, "r") as z:
                z.extractall(unzip_dir)
        except Exception:
            return []
        imgs = [
            p
            for p in unzip_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
        imgs.sort(key=lambda p: p.name.lower())
        return imgs
    return []


def try_download_presentation_assets(page, out_dir: Path) -> List[Path]:
    # Prefer true export/download from NotebookLM presentation if available.
    # Different locales/builds expose slightly different labels.
    more_selectors = [
        "button[aria-label*='更多']",
        "button[aria-label*='more' i]",
        "button:has-text('更多')",
        "button:has-text('More')",
    ]
    for sel in more_selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible(timeout=600):
                loc.first.click(timeout=1200)
                page.wait_for_timeout(500)
                break
        except Exception:
            pass

    download_selectors = [
        "button[aria-label*='下载']",
        "button[aria-label*='download' i]",
        "button:has-text('下载')",
        "button:has-text('Download')",
        "[role='menuitem']:has-text('下载')",
        "[role='menuitem']:has-text('Download')",
        "a:has-text('下载')",
        "a:has-text('Download')",
    ]
    for sel in download_selectors:
        loc = page.locator(sel)
        if loc.count() <= 0:
            continue
        try:
            with page.expect_download(timeout=7000) as dli:
                loc.first.click(timeout=1500)
            dl = dli.value
            target = out_dir / dl.suggested_filename
            dl.save_as(str(target))
            imgs = _collect_images_from_download(target, out_dir)
            if imgs:
                return imgs
        except Exception:
            continue
    return []


def ensure_signed_in(page):
    if "accounts.google.com" in page.url:
        raise RuntimeError(
            "NotebookLM profile is not signed in. 请先在该配置目录登录一次后重试: "
            f"{DEFAULT_PROFILE}"
        )


def automate_page(page, url: str, out_dir: Path, count: int) -> List[Path]:
    page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(3000)
    ensure_signed_in(page)

    # Always force a fresh notebook to avoid stale sources from previous runs.
    if "/notebook/" in page.url:
        click_any(
            page,
            [
                "a[aria-label='NotebookLM 首页']",
                "a:has-text('NotebookLM')",
                "a[href='https://notebooklm.google.com/']",
            ],
            timeout_ms=6000,
        )
        page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2500)

    _ = click_any(
        page,
        [
            "button[aria-label='新建笔记本']",
            "button.create-new-button",
            "button:has-text('新建')",
            "button:has-text('Create new')",
            "button:has-text('New notebook')",
            "button:has-text('Create')",
        ],
        timeout_ms=18000,
    )
    page.wait_for_timeout(5000)
    if "/notebook/" not in page.url:
        raise RuntimeError("NotebookLM 新建笔记本失败")

    # Some UIs already show source shortcuts. Prefer direct website entry first.
    web_opened = click_any(
        page,
        [
            "button:has-text('网站')",
            "button:has-text('Web')",
            "button:has-text('link')",
            "[role='button']:has-text('网站')",
        ],
        timeout_ms=5000,
    )
    if not web_opened:
        added = click_any(
            page,
            [
                "button:has-text('Add source')",
                "button:has-text('添加来源')",
                "button:has-text('Add')",
                "button:has-text('Source')",
                "button[aria-label*='source' i]",
                "button[aria-label*='来源']",
                "button[aria-label='添加来源']",
                "button[aria-label='打开“上传来源”对话框']",
                "[role='button']:has-text('来源')",
                "[role='button']:has-text('Source')",
                "button:has-text('Upload')",
                "button:has-text('上传')",
                "button:has-text('上传来源')",
                "button:has-text('Link')",
                "button:has-text('链接')",
                "button:has-text('+')",
            ],
            timeout_ms=18000,
        )
        if not added:
            raise RuntimeError("NotebookLM source入口未找到")
        click_any(
            page,
            [
                "button:has-text('网站')",
                "button:has-text('Web')",
                "button:has-text('link')",
                "[role='button']:has-text('网站')",
            ],
            timeout_ms=8000,
        )

    filled = fill_any(
        page,
        [
            "input[type='url']",
            "input[placeholder*='https']",
            "input[placeholder*='URL']",
            "textarea[placeholder*='网络']",
            "textarea[aria-label*='查询']",
            "textarea",
            "input[type='text']",
        ],
        url,
        timeout_ms=12000,
    )
    if not filled:
        raise RuntimeError("NotebookLM source input not found")

    submitted = click_any(
        page,
        [
            "button:has-text('Insert')",
            "button:has-text('Add')",
            "button:has-text('Save')",
            "button:has-text('完成')",
            "button:has-text('添加')",
            "button:has-text('确定')",
            "button:has-text('创建笔记本')",
            "button[aria-label='提交']",
            "button[aria-label='创建笔记本']",
        ],
        timeout_ms=10000,
    )
    if not submitted:
        page.keyboard.press("Enter")

    # Wait for source processing.
    page.wait_for_timeout(12000)
    # Validate source in a UI-friendly way: domain OR source-count hints.
    def has_source_hint(txt: str) -> bool:
        t = txt.lower()
        host = urlparse(url).netloc.replace("www.", "").lower()
        if host and host in t:
            return True
        # Common host aliases not always shown as raw domain.
        if ("youtube.com" in host or "youtu.be" in host) and "youtube" in t:
            return True
        # NotebookLM often shows source count wording instead of domain text.
        if re.search(r"基于\\s*[1-9]\\d*\\s*个来源", txt):
            return True
        if re.search(r"\\b[1-9]\\d*\\s+sources?\\b", t):
            return True
        return False

    body_text = page.inner_text("body")
    if not has_source_hint(body_text):
        # One retry on submit if source hint not observed.
        click_any(
            page,
            [
                "button[aria-label='提交']",
                "button:has-text('提交')",
                "button:has-text('创建笔记本')",
                "button:has-text('添加')",
            ],
            timeout_ms=4000,
        )
        page.wait_for_timeout(8000)
    # Close source/search overlays if present.
    click_any(
        page,
        [
            "button[aria-label='关闭']",
            "button:has-text('关闭')",
            ".close-button",
        ],
        timeout_ms=3000,
    )
    # Ensure studio panel is visible.
    click_any(
        page,
        [
            "button[aria-label*='Studio 面板']",
            "button:has-text('Studio')",
            ".toggle-studio-panel-button",
        ],
        timeout_ms=3000,
    )
    page.wait_for_timeout(2000)

    # Generate presentation (must succeed before capture).
    ppt_selectors = [
        "div.create-artifact-button-container:has-text('演示文稿')",
        "[aria-label='演示文稿']",
        "button:has-text('演示文稿')",
    ]
    clicked_ppt = click_any(page, ppt_selectors, timeout_ms=12000)
    if not clicked_ppt:
        # One retry after toggling panels.
        click_any(
            page,
            [
                "button[aria-label*='来源面板']",
                ".toggle-source-panel-button",
                "button[aria-label*='Studio 面板']",
                ".toggle-studio-panel-button",
            ],
            timeout_ms=4000,
        )
        page.wait_for_timeout(2000)
        clicked_ppt = click_any(page, ppt_selectors, timeout_ms=10000)
    if not clicked_ppt:
        raise RuntimeError("NotebookLM 演示文稿入口未找到")

    timeout_ms = int(os.environ.get("NBLM_PPT_TIMEOUT_MS", "900000"))
    try:
        wait_presentation_done(page, timeout_ms=timeout_ms)
    except Exception:
        try:
            page.screenshot(path=str(out_dir / "notebooklm-timeout-diagnostic.png"), full_page=False)
        except Exception:
            pass
        raise

    # First try true download/export if UI offers it.
    downloaded = try_download_presentation_assets(page, out_dir=out_dir)
    if downloaded:
        return downloaded[: max(1, min(6, count))]

    shots = capture_presentation_outputs(page, out_dir=out_dir, count=max(1, min(6, count)))
    if not shots:
        raise RuntimeError("NotebookLM 演示文稿已生成，但未拿到导出文件或演示文稿产物（已禁用整页截图兜底）")
    return shots


def wait_cdp_ready(port: int, timeout_sec: int = 20) -> None:
    url = f"http://127.0.0.1:{port}/json/version"
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("Chrome CDP endpoint not ready")


def run_via_cdp(p, url: str, out_dir: Path, count: int, headless: bool) -> List[Path]:
    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    port = 9222
    cmd = [
        chrome_bin,
        f"--user-data-dir={DEFAULT_PROFILE}",
        f"--remote-debugging-port={port}",
        "--new-window",
        "--disable-blink-features=AutomationControlled",
        "about:blank",
    ]
    if headless:
        cmd.insert(4, "--headless=new")

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    browser = None
    try:
        wait_cdp_ready(port)
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        return automate_page(page, url, out_dir, count)
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass


def run(url: str, out_dir: Path, count: int, headless: bool) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    DEFAULT_PROFILE.mkdir(parents=True, exist_ok=True)

    # Auto-clean stale dedicated profile processes, do not touch normal Chrome.
    kill_profile_processes(DEFAULT_PROFILE)
    # Do not fail early here; let launch/cdp fallback decide.
    cleanup_stale_singleton(DEFAULT_PROFILE)

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(DEFAULT_PROFILE),
                channel="chrome",
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--password-store=basic", "--use-mock-keychain"],
                viewport={"width": 1440, "height": 980},
            )
            try:
                page = ctx.new_page()
                return automate_page(page, url, out_dir, count)
            finally:
                ctx.close()
        except Exception as e:
            if "Browser.getWindowForTarget" not in str(e):
                raise
            return run_via_cdp(p, url, out_dir, count, headless)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--timeout-ms", type=int, default=900000)
    args = p.parse_args()

    def compact_reason(e: Exception) -> str:
        s = str(e).strip()
        if not s:
            return "unknown error"
        return s.splitlines()[0]

    try:
        # Export timeout to inner wait logic via env for easy tuning from caller.
        os.environ["NBLM_PPT_TIMEOUT_MS"] = str(args.timeout_ms)
        shots = run(args.url, Path(args.out_dir).resolve(), args.count, args.headless)
        print(json.dumps({"status": "ok", "images": [str(x) for x in shots]}, ensure_ascii=False))
        return 0
    except (RuntimeError, PlaywrightTimeoutError) as e:
        # Save one diagnostic screenshot path hint if available.
        print(json.dumps({"status": "error", "reason": compact_reason(e)}, ensure_ascii=False))
        return 2
    except Exception as e:
        print(json.dumps({"status": "error", "reason": compact_reason(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
