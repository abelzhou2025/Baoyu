#!/usr/bin/env python3
"""Render markdown (with inline images) into Xiaohongshu 3:4 page images.

Goals:
- 1080x1440 pages (3:4).
- Block-aware pagination: do not split a paragraph/image across pages.
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


def _split_long_paragraphs(blocks: List[Dict], chunk_chars: int = 320) -> List[Dict]:
    out: List[Dict] = []
    for b in blocks:
        if b.get("type") != "p":
            out.append(b)
            continue
        t = (b.get("text") or "").strip()
        if len(t) <= chunk_chars:
            out.append(b)
            continue
        start = 0
        while start < len(t):
            cut = min(len(t), start + chunk_chars)
            # Prefer breaking by Chinese punctuation.
            window = t[start:cut]
            k = max(window.rfind("。"), window.rfind("！"), window.rfind("？"), window.rfind("；"))
            if k >= int(chunk_chars * 0.55):
                cut = start + k + 1
            out.append({"type": "p", "text": t[start:cut].strip()})
            start = cut
    return out


def render_pages(md_path: Path, out_dir: Path, max_pages: int = 5) -> List[Path]:
    md = md_path.read_text(encoding="utf-8")
    blocks = parse_md_blocks(md)
    # Keep full article content; only split oversized paragraphs for pagination stability.
    blocks = _split_long_paragraphs(blocks, chunk_chars=320)
    out_dir.mkdir(parents=True, exist_ok=True)

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <style>
    :root {{
      --pad-y: 26px;
      --pad-x: 30px;
      --h1-size: 34px;
      --h2-size: 28px;
      --h3-size: 24px;
      --p-size: 20px;
      --p-lh: 1.48;
      --li-size: 19px;
      --li-lh: 1.44;
      --img-max-h: 250px;
      --block-gap: 8px;
    }}
    html, body {{
      margin: 0; padding: 0;
      background: #f5f6f8;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
    }}
    #pages {{
      width: 1080px;
      margin: 0 auto;
      padding: 24px 0;
      box-sizing: border-box;
    }}
    .xhs-page {{
      width: 1080px;
      height: 1440px;
      background: #ffffff;
      border-radius: 16px;
      overflow: hidden;
      box-shadow: 0 8px 28px rgba(0,0,0,0.08);
      margin: 0 0 24px 0;
      box-sizing: border-box;
      position: relative;
    }}
    .inner {{
      padding: var(--pad-y) var(--pad-x);
      box-sizing: border-box;
      height: 100%;
      overflow: hidden;
    }}
    h1, h2, h3 {{ margin: 0 0 8px 0; line-height: 1.28; color: #111827; }}
    h1 {{ font-size: var(--h1-size); }}
    h2 {{ font-size: var(--h2-size); }}
    h3 {{ font-size: var(--h3-size); }}
    p {{
      margin: 0 0 var(--block-gap) 0;
      line-height: var(--p-lh);
      font-size: var(--p-size);
      color: #1f2937;
      word-break: break-word;
    }}
    ul {{ margin: 0 0 var(--block-gap) 22px; padding: 0; }}
    li {{
      line-height: var(--li-lh);
      font-size: var(--li-size);
      color: #1f2937;
      margin: 0 0 6px 0;
    }}
    .img-wrap {{ margin: 6px 0 10px 0; text-align: center; }}
    .img-wrap img {{
      max-width: 100%;
      max-height: var(--img-max-h);
      border-radius: 12px;
      object-fit: contain;
      background: #fff;
    }}
  </style>
</head>
<body>
  <div id="pages"></div>
  <script>
    const blocks = {json.dumps(blocks, ensure_ascii=False)};
    const pageH = 1440;
    const maxPages = {max(1, max_pages)};
    const pagesRoot = document.getElementById('pages');

    function el(tag, cls) {{
      const n = document.createElement(tag);
      if (cls) n.className = cls;
      return n;
    }}

    function buildBlock(b) {{
      if (b.type === "img") {{
        const w = el("div", "img-wrap");
        const img = document.createElement("img");
        img.alt = b.alt || "";
        img.src = b.src;
        w.appendChild(img);
        return w;
      }}
      if (b.type === "ul") {{
        const u = document.createElement("ul");
        (b.items || []).forEach(t => {{
          const li = document.createElement("li");
          li.textContent = t;
          u.appendChild(li);
        }});
        return u;
      }}
      const tag = ["h1","h2","h3"].includes(b.type) ? b.type : "p";
      const n = document.createElement(tag);
      n.textContent = b.text || "";
      return n;
    }}

    function newPage(idx) {{
      const p = el("section", "xhs-page");
      p.setAttribute("data-index", String(idx));
      const inner = el("div", "inner");
      p.appendChild(inner);
      pagesRoot.appendChild(p);
      return inner;
    }}

    function setPreset(p) {{
      const rs = document.documentElement.style;
      rs.setProperty('--pad-y', p.padY + 'px');
      rs.setProperty('--pad-x', p.padX + 'px');
      rs.setProperty('--h1-size', p.h1 + 'px');
      rs.setProperty('--h2-size', p.h2 + 'px');
      rs.setProperty('--h3-size', p.h3 + 'px');
      rs.setProperty('--p-size', p.p + 'px');
      rs.setProperty('--p-lh', String(p.plh));
      rs.setProperty('--li-size', p.li + 'px');
      rs.setProperty('--li-lh', String(p.llh));
      rs.setProperty('--img-max-h', p.imgH + 'px');
      rs.setProperty('--block-gap', p.gap + 'px');
    }}

    async function waitImages(nodes) {{
      const tasks = [];
      for (const n of nodes) {{
        const imgs = n.querySelectorAll ? n.querySelectorAll("img") : [];
        imgs.forEach(img => {{
          if (!img.complete) {{
            tasks.push(new Promise(r => {{
              img.onload = () => r();
              img.onerror = () => r();
            }}));
          }}
        }});
      }}
      if (tasks.length) await Promise.all(tasks);
    }}

    function paginateOnce(staged) {{
      pagesRoot.innerHTML = "";
      let idx = 0;
      let cur = newPage(idx);
      const padY = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--pad-y')) || 44;
      const usable = pageH - (padY * 2);

      for (const node of staged) {{
        cur.appendChild(node);
        if (cur.scrollHeight > usable) {{
          cur.removeChild(node);
          idx += 1;
          cur = newPage(idx);
          cur.appendChild(node);
        }}
      }}
      return idx + 1;
    }}

    window.paginateNow = async function() {{
      const staged = blocks.map(buildBlock);
      await waitImages(staged);

      const imgCount = blocks.filter(b => b.type === "img").length;
      const presets = [
        {{ padY: 26, padX: 30, h1: 34, h2: 28, h3: 24, p: 20, plh: 1.48, li: 19, llh: 1.44, imgH: imgCount >= 3 ? 250 : 320, gap: 8 }},
        {{ padY: 22, padX: 26, h1: 31, h2: 26, h3: 22, p: 18, plh: 1.40, li: 17, llh: 1.36, imgH: imgCount >= 3 ? 220 : 280, gap: 6 }},
        {{ padY: 18, padX: 22, h1: 28, h2: 24, h3: 20, p: 16, plh: 1.34, li: 15, llh: 1.30, imgH: imgCount >= 3 ? 190 : 240, gap: 4 }},
        {{ padY: 16, padX: 20, h1: 26, h2: 22, h3: 18, p: 15, plh: 1.28, li: 14, llh: 1.24, imgH: imgCount >= 3 ? 170 : 220, gap: 3 }}
      ];

      let finalCount = 0;
      for (const p of presets) {{
        setPreset(p);
        finalCount = paginateOnce(staged);
        if (finalCount <= maxPages) break;
      }}
      window.__PAGE_COUNT = finalCount;
    }};
  </script>
</body>
</html>
"""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1080, "height": 1440})
        page.set_content(html, wait_until="domcontentloaded")
        page.evaluate("() => window.paginateNow()")
        count = int(page.evaluate("() => window.__PAGE_COUNT || 0"))
        outs: List[Path] = []
        for i in range(count):
            tgt = out_dir / f"xhs-{i+1:02d}.jpg"
            loc = page.locator(f".xhs-page[data-index='{i}']")
            loc.screenshot(path=str(tgt), type="jpeg", quality=88)
            outs.append(tgt)
        browser.close()
    return outs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--max-pages", type=int, default=5)
    args = ap.parse_args()

    md_path = Path(args.md).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if not md_path.exists():
        raise SystemExit(f"markdown not found: {md_path}")

    pages = render_pages(md_path=md_path, out_dir=out_dir, max_pages=max(1, args.max_pages))
    print(json.dumps({"status": "ok", "count": len(pages), "pages": [str(x) for x in pages]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
