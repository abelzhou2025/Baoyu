#!/usr/bin/env python3
"""Import real NotebookLM-exported slides/images into a markdown file.

Accepted input:
- image file (.png/.jpg/.jpeg/.webp)
- directory containing images
- .zip containing images

Output:
- same markdown file, with an added section and inline data-uri images
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import re
import zipfile
from pathlib import Path
from typing import List

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def is_img(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in IMG_EXTS


def natural_key(name: str):
    parts = re.split(r"(\d+)", name.lower())
    out = []
    for x in parts:
        if x.isdigit():
            out.append(int(x))
        else:
            out.append(x)
    return out


def list_images(src: Path) -> List[Path]:
    if is_img(src):
        return [src]

    if src.is_dir():
        imgs = [p for p in src.iterdir() if is_img(p)]
        return sorted(imgs, key=lambda p: natural_key(p.name))

    if src.is_file() and src.suffix.lower() == ".zip":
        out_dir = src.parent / f".notebooklm-unzip-{src.stem}"
        out_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src, "r") as z:
            z.extractall(out_dir)
        imgs = [p for p in out_dir.rglob("*") if is_img(p)]
        return sorted(imgs, key=lambda p: natural_key(str(p.relative_to(out_dir))))

    return []


def to_data_uri(p: Path) -> str:
    mime, _ = mimetypes.guess_type(str(p))
    mime = mime or "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def add_section(md: str, uris: List[str], title: str) -> str:
    text = md.rstrip() + "\n"

    # Replace existing placeholder if present.
    ph = re.compile(r"(?ms)^##\s*可视化图卡（NotebookLM风格）\s*$.*?(?=^##\s|\Z)")
    if ph.search(text):
        block = [f"## {title}"]
        for i, u in enumerate(uris, start=1):
            block.append(f"![NotebookLM图卡{i}]({u})")
        repl = "\n".join(block) + "\n\n"
        return ph.sub(repl, text, count=1).rstrip() + "\n"

    insert = ["", f"## {title}"]
    for i, u in enumerate(uris, start=1):
        insert.append(f"![NotebookLM图卡{i}]({u})")
    return text.rstrip() + "\n" + "\n".join(insert) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--md", required=True, help="target markdown file")
    p.add_argument("--slides", required=True, help="image file / dir / zip exported from NotebookLM")
    p.add_argument("--max", type=int, default=3)
    p.add_argument("--title", default="可视化图卡（NotebookLM原生导出）")
    args = p.parse_args()

    md_path = Path(args.md).expanduser().resolve()
    src = Path(args.slides).expanduser().resolve()

    if not md_path.exists():
        raise SystemExit(f"markdown not found: {md_path}")
    if not src.exists():
        raise SystemExit(f"slides source not found: {src}")

    imgs = list_images(src)
    if not imgs:
        raise SystemExit("no images found. 请导出 PNG/JPG，或提供包含图片的目录/zip")

    max_n = max(1, min(10, args.max))
    imgs = imgs[:max_n]
    uris = [to_data_uri(p) for p in imgs]

    md = md_path.read_text(encoding="utf-8")
    out = add_section(md, uris, args.title)
    md_path.write_text(out, encoding="utf-8")

    print(f"ok: {md_path}")
    print(f"imported_images: {len(imgs)}")
    for i, p in enumerate(imgs, start=1):
        print(f"- {i}: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
