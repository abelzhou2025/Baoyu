#!/usr/bin/env python3
"""Render markdown and export fixed-count 3:4 slices for Xiaohongshu.

This script is intentionally simple:
- render full markdown as one long 1080px-wide page
- slice into fixed N images of 1080x1440 by evenly distributed viewport captures
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

from playwright.sync_api import sync_playwright


def parse_md_blocks(md: str) -> List[Dict]:
    lines = md.splitlines()
    blocks: List[Dict] = []
    para: List[str] = []
    list_buf: List[str] = []

    def flush_para():
        nonlocal para
        if para:
            txt = " ".join(x.strip() for x in para if x.strip()).strip()
            if txt:
                blocks.append({"type": "p", "text": txt})
            para = []

    def flush_list():
        nonlocal list_buf
        if list_buf:
            blocks.append({"type": "ul", "items": list_buf[:]})
            list_buf = []

    img_pat = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")
    head_pat = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    li_pat = re.compile(r"^\s*-\s+(.+?)\s*$")

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            flush_list()
            continue

        m_img = img_pat.match(line)
        if m_img:
            flush_para()
            flush_list()
            blocks.append({"type": "img", "alt": m_img.group(1), "src": m_img.group(2)})
            continue

        m_head = head_pat.match(line)
        if m_head:
            flush_para()
            flush_list()
            level = min(3, len(m_head.group(1)))
            blocks.append({"type": f"h{level}", "text": m_head.group(2).strip()})
            continue

        m_li = li_pat.match(line)
        if m_li:
            flush_para()
            list_buf.append(m_li.group(1).strip())
            continue

        flush_list()
        para.append(line)

    flush_para()
    flush_list()
    return blocks


def build_html(blocks: List[Dict]) -> str:
    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    body_parts: List[str] = []
    for b in blocks:
        t = b.get("type")
        if t == "img":
            alt = esc(str(b.get("alt") or ""))
            src = str(b.get("src") or "")
            body_parts.append(f"<figure><img alt=\"{alt}\" src=\"{src}\" /></figure>")
        elif t == "ul":
            items = b.get("items") or []
            lis = "".join(f"<li>{esc(str(x))}</li>" for x in items)
            body_parts.append(f"<ul>{lis}</ul>")
        elif t in {"h1", "h2", "h3"}:
            body_parts.append(f"<{t}>{esc(str(b.get('text') or ''))}</{t}>")
        else:
            body_parts.append(f"<p>{esc(str(b.get('text') or ''))}</p>")

    body_html = "\n".join(body_parts)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      background: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
      color: #111827;
    }}
    .doc {{
      width: 1080px;
      margin: 0 auto;
      box-sizing: border-box;
      padding: 34px 34px 42px 34px;
    }}
    h1 {{ font-size: 40px; line-height: 1.25; margin: 0 0 14px 0; }}
    h2 {{ font-size: 32px; line-height: 1.3; margin: 14px 0 12px 0; }}
    h3 {{ font-size: 27px; line-height: 1.3; margin: 12px 0 10px 0; }}
    p {{
      margin: 0 0 12px 0;
      font-size: 22px;
      line-height: 1.56;
      word-break: break-word;
    }}
    ul {{ margin: 0 0 12px 24px; padding: 0; }}
    li {{ font-size: 21px; line-height: 1.5; margin: 0 0 7px 0; }}
    figure {{ margin: 10px 0 14px 0; }}
    img {{
      display: block;
      width: 100%;
      height: auto;
      max-height: 560px;
      object-fit: contain;
      border-radius: 12px;
    }}
  </style>
</head>
<body>
  <main class="doc">
    {body_html}
  </main>
</body>
</html>
"""


def export_fixed_slices(md_path: Path, out_dir: Path, count: int = 5) -> List[Path]:
    blocks = parse_md_blocks(md_path.read_text(encoding="utf-8"))
    html = build_html(blocks)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = max(1, count)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1080, "height": 1440})
        page.set_content(html, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        page.evaluate(
            "() => Promise.all(Array.from(document.images).map(img => img.complete ? Promise.resolve() : new Promise(r => { img.onload=()=>r(); img.onerror=()=>r(); })))"
        )

        total_h = int(page.evaluate("() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"))
        frame_h = 1440

        if total_h <= frame_h:
            ys = [0 for _ in range(n)]
        elif n == 1:
            ys = [0]
        else:
            max_start = total_h - frame_h
            ys = [round(i * max_start / (n - 1)) for i in range(n)]

        max_scroll = int(
            page.evaluate(
                "() => Math.max(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight) - window.innerHeight)"
            )
        )
        outs: List[Path] = []
        for i, y in enumerate(ys, start=1):
            target = out_dir / f"xhs-{i:02d}.jpg"
            yy = max(0, min(int(y), max_scroll))
            page.evaluate("(v) => window.scrollTo(0, v)", yy)
            page.wait_for_timeout(80)
            page.screenshot(path=str(target), type="jpeg", quality=88)
            outs.append(target)

        browser.close()
    return outs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--count", type=int, default=5)
    args = ap.parse_args()

    md_path = Path(args.md).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if not md_path.exists():
        raise SystemExit(f"markdown not found: {md_path}")

    pages = export_fixed_slices(md_path=md_path, out_dir=out_dir, count=max(1, args.count))
    print(json.dumps({"status": "ok", "count": len(pages), "pages": [str(x) for x in pages]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
