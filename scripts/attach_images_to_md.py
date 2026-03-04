#!/usr/bin/env python3
"""Ensure all generated images are referenced in markdown."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--md", required=True)
    p.add_argument("--images-dir", required=True)
    args = p.parse_args()

    md_path = Path(args.md)
    images_dir = Path(args.images_dir)

    if not md_path.exists() or not images_dir.exists():
        return 0

    content = md_path.read_text(encoding="utf-8")
    image_files = sorted(images_dir.glob("*.png"))
    if not image_files:
        return 0

    missing = []
    for img in image_files:
        rel = f"images/{img.name}"
        if rel not in content:
            missing.append(rel)

    if missing:
        lines = ["", "## 图集", ""]
        for rel in missing:
            lines.append(f"![{Path(rel).stem}]({rel})")
            lines.append("")
        content = content.rstrip() + "\n" + "\n".join(lines)

    # Remove image references that do not exist on disk.
    lines = content.splitlines()
    kept = []
    pat = re.compile(r"!\[[^\]]*\]\((images/[^)]+)\)")
    for line in lines:
        m = pat.search(line)
        if not m:
            kept.append(line)
            continue
        rel = m.group(1)
        if (md_path.parent / rel).exists():
            kept.append(line)

    md_path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
